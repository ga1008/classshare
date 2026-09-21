/** Content-address the complete native CSS/ESM graph without bundling legacy globals.
 * Run after Tailwind and Vite. Existing snapshots are never removed by a build.
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join, posix, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import { init, parse } from 'es-module-lexer';
import { isNativeLqModule, nativeLqCompiler, printNativeLqModule } from './ui/native_lq_print.mjs';

const RECIPE = 'lanshare-static-graph-v1';
const TEXT_EXT = /\.(?:css|js|mjs|svg|json)$/;
const GENERATED_OR_SOURCE = /(?:\.src\.css|\.test\.js|\.d\.ts|\.map|\.gz|\.br)$/;
const OWNED_PREFIX = /\/static\/(?:js\/|css\/|vendor\/|fonts\/|google_css\.css)/g;

function atomicWrite(path, bytes) {
  mkdirSync(dirname(path), { recursive: true });
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, bytes);
  renameSync(temporary, path);
}

function immutableWrite(path, bytes) {
  if (existsSync(path)) {
    if (!readFileSync(path).equals(bytes)) throw new Error(`Immutable asset collision: ${path}`);
    return;
  }
  atomicWrite(path, bytes);
}

function localReference(specifier, owner) {
  if (!specifier || /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(specifier)) return null;
  const [path] = specifier.split(/[?#]/);
  if (path.startsWith('/static/')) return posix.normalize(path.slice(8));
  if (path.startsWith('/')) return null;
  return posix.normalize(posix.join(posix.dirname(owner), path));
}

function withoutManualVersion(specifier) {
  const hashAt = specifier.indexOf('#');
  const fragment = hashAt < 0 ? '' : specifier.slice(hashAt);
  const bare = hashAt < 0 ? specifier : specifier.slice(0, hashAt);
  const queryAt = bare.indexOf('?');
  if (queryAt < 0) return specifier;
  const query = new URLSearchParams(bare.slice(queryAt + 1));
  query.delete('v');
  return bare.slice(0, queryAt) + (query.size ? `?${query}` : '') + fragment;
}

export function staticAssetRecipeHash() {
  return createHash('sha256').update(RECIPE + '\0')
    .update(readFileSync(fileURLToPath(import.meta.url))).update('\0')
    .update(readFileSync(new URL('./ui/native_lq_print.mjs', import.meta.url))).update('\0')
    .update(nativeLqCompiler).digest('hex');
}

/** Inspect the same complete inputs used by a build, without writing an artifact. */
export async function inspectStaticInputs(staticRoot) {
  await init;
  const root = resolve(staticRoot);
  const sources = new Map();
  const add = relative => {
    if (relative.startsWith('../') || posix.isAbsolute(relative)) throw new Error(`Asset escapes static root: ${relative}`);
    if (sources.has(relative)) return;
    const absolute = join(root, relative);
    if (!existsSync(absolute)) throw new Error(`Missing static graph dependency: ${relative}`);
    sources.set(relative, readFileSync(absolute));
  };
  const walk = relative => {
    if (!existsSync(join(root, relative))) return;
    for (const entry of readdirSync(join(root, relative), { withFileTypes: true })) {
      const path = posix.join(relative, entry.name);
      if (entry.isSymbolicLink()) throw new Error(`Symlink is not a static build input: ${path}`);
      if (entry.isDirectory()) walk(path);
      else if (!GENERATED_OR_SOURCE.test(path) && path !== 'vendor/manifest.json') add(path);
    }
  };
  // Only application code and its fixed vendor/font dependencies are snapshotted.
  // Live login backgrounds, lesson uploads and other media are not copied.
  for (const directory of ['css', 'js', 'vendor', 'fonts']) walk(directory);
  if (existsSync(join(root, 'google_css.css'))) add('google_css.css');

  const moduleImports = new Map();
  for (const [relative, bytes] of sources) {
    if (/\.(?:js|mjs)$/.test(relative)) {
      const [imports] = parse(bytes.toString('utf8'), relative);
      moduleImports.set(relative, imports);
      for (const item of imports) {
        if (item.n == null || (!item.n.startsWith('.') && !item.n.startsWith('/static/'))) continue;
        const dependency = localReference(item.n, relative);
        if (dependency) add(dependency);
      }
    } else if (relative.endsWith('.css')) {
      for (const match of bytes.toString('utf8').matchAll(/(?:url\(\s*|@import\s+)(?:["']?)([^\s"')]+)["']?\s*\)?/g)) {
        const dependency = localReference(match[1], relative);
        if (dependency) add(dependency);
      }
    }
  }

  // A printer implementation/compiler change must never reuse an immutable URL.
  const recipeHash = staticAssetRecipeHash();
  const hash = createHash('sha256').update(recipeHash + '\0');
  const sourceHashes = {};
  // Bind the Vite entry graph to this native graph; its filenames are already hashed.
  const viteManifest = join(root, 'dist', 'manifest.json');
  if (existsSync(viteManifest)) hash.update(readFileSync(viteManifest));
  for (const [relative, bytes] of [...sources].sort(([a], [b]) => a.localeCompare(b, 'en'))) {
    hash.update(relative).update('\0').update(String(bytes.length)).update('\0').update(bytes);
    sourceHashes[relative] = createHash('sha256').update(bytes).digest('hex');
  }
  const revision = hash.digest('hex');
  return { root, sources, moduleImports, revision, recipeHash, sourceHashes };
}

export async function buildStaticAssets(staticRoot) {
  const { root, sources, moduleImports, revision, recipeHash, sourceHashes } = await inspectStaticInputs(staticRoot);
  const prefix = `/static/assets/${revision}/`;
  const entries = {};
  let totalBytes = 0;
  for (const [relative, original] of sources) {
    let bytes = original;
    if (TEXT_EXT.test(relative)) {
      let text = original.toString('utf8');
      for (const item of [...(moduleImports.get(relative) || [])].reverse()) {
        if (item.n == null || (!item.n.startsWith('.') && !item.n.startsWith('/static/'))) continue;
        const normalized = withoutManualVersion(item.n);
        const specifier = normalized.startsWith('/static/') ? prefix + normalized.slice(8) : normalized;
        // Dynamic import spans include string quotes; static import spans do not.
        const replacement = item.d >= 0 ? JSON.stringify(specifier) : specifier;
        text = text.slice(0, item.s) + replacement + text.slice(item.e);
      }
      if (relative.endsWith('.css')) {
        text = text.replace(/\/static\/([^\s"')?#]+)/g, (url, path) => sources.has(path) ? prefix + path : url);
      }
      text = text.replace(OWNED_PREFIX, path => prefix + path.slice(8));
      // Print only after every import/runtime-owned URL has its final spelling.
      text = printNativeLqModule(relative, text);
      bytes = Buffer.from(text);
    }
    const output = `assets/${revision}/${relative}`;
    immutableWrite(join(root, output), bytes);
    if (TEXT_EXT.test(relative) && (bytes.length >= 256 || isNativeLqModule(relative))) {
      immutableWrite(join(root, `${output}.gz`), gzipSync(bytes, { level: 9 }));
    }
    entries[relative] = output;
    totalBytes += bytes.length;
  }
  // Vite's generated immutable files use the same precompression contract.
  const viteAssets = join(root, 'dist', 'assets');
  if (existsSync(viteAssets)) {
    for (const entry of readdirSync(viteAssets, { withFileTypes: true })) {
      if (!entry.isFile() || !TEXT_EXT.test(entry.name)) continue;
      const bytes = readFileSync(join(viteAssets, entry.name));
      atomicWrite(join(viteAssets, `${entry.name}.gz`), gzipSync(bytes, { level: 9 }));
    }
  }
  const manifest = { schema: 1, revision, entries, recipeHash, sourceHashes };
  atomicWrite(join(root, 'assets', 'manifest.json'), Buffer.from(JSON.stringify(manifest, null, 2) + '\n'));
  return { revision, files: sources.size, bytes: totalBytes };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = await buildStaticAssets(process.argv[2] || 'static');
  console.log(`Static graph ${result.revision}: ${result.files} files, ${result.bytes} bytes (gzip prepared)`);
}

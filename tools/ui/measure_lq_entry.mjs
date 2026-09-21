/** Count separate HTTP responses for the entire eager native LQ graph. */
import { readFile } from 'node:fs/promises';
import { resolve, dirname, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync, gunzipSync } from 'node:zlib';
import { init, parse } from 'es-module-lexer';
import { inspectStaticInputs } from '../build_static_assets.mjs';

const defaultStaticRoot = fileURLToPath(new URL('../../static/', import.meta.url));
const entry = 'js/lq/index.js';
const budget = 18 * 1024;

export async function measureLqEntry({ staticRoot = defaultStaticRoot, production = false } = {}) {
  await init;
  const root = resolve(staticRoot);
  let manifest;
  const sourceByOutput = new Map();
  if (production) {
    manifest = JSON.parse(await readFile(resolve(root, 'assets/manifest.json'), 'utf8'));
    if (manifest.schema !== 1 || !/^[a-f0-9]{64}$/.test(manifest.revision) || !manifest.entries) {
      throw new Error('Invalid static asset manifest');
    }
    const inputs = await inspectStaticInputs(root);
    if (manifest.recipeHash !== inputs.recipeHash) throw new Error('Stale static manifest: build recipe/compiler changed; rebuild assets');
    if (!manifest.sourceHashes || Object.keys(manifest.sourceHashes).length !== inputs.sources.size
      || Object.entries(inputs.sourceHashes).some(([path, hash]) => manifest.sourceHashes[path] !== hash)) {
      throw new Error('Stale static manifest: source hashes changed; rebuild assets');
    }
    if (manifest.revision !== inputs.revision) throw new Error('Stale static manifest: graph inputs changed; rebuild assets');
    if (Object.keys(manifest.entries).length !== inputs.sources.size) throw new Error('Incomplete static manifest entries');
    for (const path of inputs.sources.keys()) {
      const output = `assets/${manifest.revision}/${path}`;
      if (manifest.entries[path] !== output) throw new Error(`Invalid static manifest entry: ${path}`);
      sourceByOutput.set(resolve(root, output), path);
    }
  }
  const seen = new Map();
  async function visit(file) {
    if (seen.has(file)) return;
    if (!file.startsWith(root + sep)) throw new Error('LQ import escapes static root');
    if (production && !sourceByOutput.has(file)) throw new Error(`Eager import leaves current immutable graph: ${file}`);
    const bytes = await readFile(file);
    let compressed;
    if (production) {
      // Use the actual shipped sidecar, never a fresh compression estimate.
      compressed = await readFile(`${file}.gz`);
      if (!gunzipSync(compressed).equals(bytes)) throw new Error(`Gzip sidecar differs from artifact: ${file}`);
    } else compressed = gzipSync(bytes, { level: 9 });
    seen.set(file, { path: `static/${relative(root, file).split(sep).join('/')}`,
      ...(production ? { source: sourceByOutput.get(file) } : {}), bytes: bytes.length, gzip: compressed.length });
    const [imports] = parse(bytes.toString('utf8'), file);
    for (const item of imports) {
      // Literal and runtime-dependent dynamic imports belong to their consumer.
      if (item.d >= 0 || item.n == null) continue;
      const name = item.n.split(/[?#]/)[0];
      if (name.startsWith('.')) await visit(resolve(dirname(file), name));
      else if (name.startsWith('/static/')) await visit(resolve(root, name.slice(8)));
      else throw new Error(`Unexpected eager external dependency: ${name}`);
    }
  }
  const entryPath = production ? manifest.entries[entry] : entry;
  if (!entryPath) throw new Error(`Missing LQ entry: ${entry}`);
  await visit(resolve(root, entryPath));
  const modules = [...seen.values()].sort((a, b) => b.gzip - a.gzip || a.path.localeCompare(b.path));
  const gzip = modules.reduce((sum, item) => sum + item.gzip, 0);
  return { mode: production ? 'production' : 'source', entry: `static/${entryPath}`,
    ...(production ? { revision: manifest.revision, recipeHash: manifest.recipeHash, sourceHashesVerified: true } : {}),
    gzip, budget, passed: gzip <= budget,
    method: production
      ? 'Sum of verified shipped gzip sidecars for every unique eager native module; separate HTTP responses'
      : 'Unbuilt source gzip estimate (level 9); no URL rewriting or printing; not production acceptance', modules };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let staticRoot = defaultStaticRoot;
  let production = false;
  let check = false;
  let source = false;
  for (let index = 2; index < process.argv.length; index++) {
    const argument = process.argv[index];
    if (argument === '--production') production = true;
    else if (argument === '--source') source = true;
    else if (argument === '--check') check = true;
    else if (argument === '--static-root' && process.argv[index + 1] && !process.argv[index + 1].startsWith('--')) staticRoot = process.argv[++index];
    else throw new Error(`Unknown or incomplete argument: ${argument}`);
  }
  if (production && source) throw new Error('Choose either --source or --production');
  const report = await measureLqEntry({ staticRoot, production });
  console.log(JSON.stringify(report, null, 2));
  if (check && !report.passed) process.exitCode = 1;
}

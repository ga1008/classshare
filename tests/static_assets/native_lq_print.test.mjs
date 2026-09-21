import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, unlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { createHash, randomBytes } from 'node:crypto';
import { gzipSync, gunzipSync } from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { init, parse } from 'es-module-lexer';
import { buildStaticAssets } from '../../tools/build_static_assets.mjs';
import { isNativeLqModule, printNativeLqModule } from '../../tools/ui/native_lq_print.mjs';
import { measureLqEntry } from '../../tools/ui/measure_lq_entry.mjs';

const repository = resolve(import.meta.dirname, '../..');
function fixture(t) {
  const temporary = mkdtempSync(join(tmpdir(), 'lanshare-lq-print-'));
  t.after(() => rmSync(temporary, { recursive: true, force: true }));
  const root = join(temporary, 'static');
  writeFileSync(join(temporary, 'package.json'), '{"type":"module"}');
  const write = (path, text) => { mkdirSync(dirname(join(root, path)), { recursive: true }); writeFileSync(join(root, path), text); };
  const manifest = () => JSON.parse(readFileSync(join(root, 'assets/manifest.json'), 'utf8'));
  const artifact = path => join(root, manifest().entries[path]);
  write('js/lq/index.js', "export { seed } from './seed.js'; export const load = () => import('./lazy.js');");
  write('js/lq/seed.js', 'export const seed = { value: 7 };');
  write('js/lq/lazy.js', "export { seed } from './seed.js';");
  return { root, write, manifest, artifact };
}

test('printer scope is exact; legacy global/other module text is untouched', () => {
  for (const path of ['js/lq/index.js', 'js/lq/icons.generated.js', 'js/ui_overlay_motion.js', 'js/ui_popover_geometry.js']) assert.equal(isNativeLqModule(path), true);
  const source = '/* ordinary comment */ var globalBusinessValue = 1;\n';
  for (const path of ['js/ui.js', 'js/lq_nested.js', 'js/lq/child/nested.js', 'js/lq/module.mjs', 'css/lq/test.js']) {
    assert.equal(isNativeLqModule(path), false);
    assert.equal(printNativeLqModule(path, source), source);
  }
});

test('emitted ESM preserves exports, names, Unicode, regex, raw templates and lazy singleton; license stays adjacent', async t => {
  const f = fixture(t);
  const source = [
    '/*! lucide-react ISC/MIT; license: ./lucide-LICENSE.txt */',
    '// discard this ordinary formatting comment',
    "import { seed } from './seed.js?v=first';",
    "export { seed as renamedSeed } from './seed.js?v=second';",
    "export const 中文 = '导航 🧭';",
    'export const pattern = /[中文\\p{Letter}]+\\/path/gu;',
    'export const raw = String.raw`字\\u0061\\n${"插值"}`;',
    'const keepUnusedLocal = "no dead code elimination";',
    'export function descriptiveFunctionName(value = seed.value) { return value + 1; }',
    "export const load = () => import('./lazy.js?v=old');",
    'export default seed;',
  ].join('\n');
  f.write('js/lq/index.js', source);
  const license = readFileSync(join(repository, 'static/js/lq/lucide-LICENSE.txt'));
  f.write('js/lq/lucide-LICENSE.txt', license);
  f.write('js/business.js', '// retain legacy formatting\nvar legacyBusinessName = 4;\n');
  await buildStaticAssets(f.root);
  const emitted = readFileSync(f.artifact('js/lq/index.js'), 'utf8');
  assert.ok(emitted.length < source.length);
  assert.ok(emitted.includes('license: ./lucide-LICENSE.txt'));
  assert.ok(emitted.includes('keepUnusedLocal'));
  assert.ok(!emitted.includes('discard this ordinary'));
  assert.ok(!emitted.includes('?v='));
  assert.deepEqual(readFileSync(f.artifact('js/lq/lucide-LICENSE.txt')), license);
  assert.equal(readFileSync(f.artifact('js/business.js'), 'utf8'), '// retain legacy formatting\nvar legacyBusinessName = 4;\n');
  const result = await import(pathToFileURL(f.artifact('js/lq/index.js')).href);
  assert.deepEqual(Object.keys(result), ['default', 'descriptiveFunctionName', 'load', 'pattern', 'raw', 'renamedSeed', '中文']);
  assert.equal(result.中文, '导航 🧭');
  assert.equal(result.pattern.source, '[中文\\p{Letter}]+\\/path');
  assert.equal(result.pattern.flags, 'gu');
  assert.equal(result.raw, '字\\u0061\\n插值');
  assert.equal(result.descriptiveFunctionName.name, 'descriptiveFunctionName');
  assert.equal(result.descriptiveFunctionName(), 8);
  assert.equal(result.default, result.renamedSeed);
  assert.equal((await result.load()).seed, result.default);
});

test('local shortening preserves closure state, shorthand properties and reflective names without dropping code', async t => {
  const f = fixture(t);
  f.write('js/lq/index.js', `
    const topLevelContract = 'retained';
    export function makeController(initialValue) {
      let currentValue = initialValue;
      const namedCallback = () => ++currentValue;
      class LocalController { value() { return currentValue; } }
      function describeName() { return namedCallback.name + '/' + LocalController.name; }
      const localOnlyUnused = 'KEEP_UNUSED_LITERAL';
      return { namedCallback, LocalController, describeName, initialValue, topLevelContract };
    }
  `);
  await buildStaticAssets(f.root);
  const emitted = readFileSync(f.artifact('js/lq/index.js'), 'utf8');
  assert.ok(emitted.includes('topLevelContract'));
  assert.ok(emitted.includes('KEEP_UNUSED_LITERAL'));
  const { makeController } = await import(pathToFileURL(f.artifact('js/lq/index.js')).href);
  assert.equal(makeController.name, 'makeController');
  const first = makeController(7), second = makeController(21);
  assert.deepEqual(Object.keys(first), ['namedCallback', 'LocalController', 'describeName', 'initialValue', 'topLevelContract']);
  assert.equal(first.namedCallback(), 8);
  assert.equal(new first.LocalController().value(), 8);
  assert.equal(second.namedCallback(), 22);
  assert.equal(first.describeName(), 'namedCallback/LocalController');
  assert.equal(first.initialValue, 7);
  assert.equal(first.topLevelContract, 'retained');
});

test('URL rewriting precedes printing for eager, dynamic and owned runtime URLs', async t => {
  const f = fixture(t);
  f.write('js/lq/index.js', "export { seed } from '/static/js/lq/seed.js?v=old&mode=one#tag'; export const load = () => import('/static/js/lq/lazy.js?v=old'); export const runtime = '/static/js/lq/lazy.js';");
  await buildStaticAssets(f.root);
  const { revision } = f.manifest();
  const emitted = readFileSync(f.artifact('js/lq/index.js'), 'utf8');
  await init;
  const imports = parse(emitted)[0];
  assert.deepEqual(imports.filter(item => item.d < 0).map(item => item.n), [
    `/static/assets/${revision}/js/lq/seed.js?mode=one#tag`,
  ]);
  // The printer may choose a no-substitution template for an import string.
  assert.equal(imports.filter(item => item.d >= 0).length, 1);
  assert.ok(emitted.slice(imports[1].s, imports[1].e).includes(`/static/assets/${revision}/js/lq/lazy.js`));
  assert.ok(!emitted.includes('?v='));
  assert.ok(emitted.includes(`/static/assets/${revision}/js/lq/lazy.js`));
  assert.ok(!emitted.includes('/static/js/'));
});

test('syntax errors fail rather than silently printing or publishing a new manifest', async t => {
  const f = fixture(t);
  await buildStaticAssets(f.root);
  const before = readFileSync(join(f.root, 'assets/manifest.json'));
  f.write('js/lq/seed.js', 'export const broken = ;');
  await assert.rejects(buildStaticAssets(f.root), /Cannot print native LQ module js\/lq\/seed.js/);
  assert.deepEqual(readFileSync(join(f.root, 'assets/manifest.json')), before);
});

test('printer source and compiler version changes each force new immutable URLs; manifest binds every source', async t => {
  const f = fixture(t);
  // Copies resolve the installed lexer above .codex-temp. The compiler facade
  // uses the real printer but allows a version-only upgrade without editing npm.
  const parent = join(repository, '.codex-temp'); mkdirSync(parent, { recursive: true });
  const toolsCopy = mkdtempSync(join(parent, 'lq-printer-recipe-'));
  t.after(() => rmSync(toolsCopy, { recursive: true, force: true }));
  mkdirSync(join(toolsCopy, 'ui'));
  const builder = join(toolsCopy, 'build_static_assets.mjs');
  const printer = join(toolsCopy, 'ui/native_lq_print.mjs');
  writeFileSync(builder, readFileSync(join(repository, 'tools/build_static_assets.mjs')));
  writeFileSync(printer, readFileSync(join(repository, 'tools/ui/native_lq_print.mjs')));
  const compiler = join(toolsCopy, 'node_modules/rolldown'); mkdirSync(compiler, { recursive: true });
  const compilerEntry = pathToFileURL(createRequire(import.meta.url).resolve('rolldown/experimental')).href;
  writeFileSync(join(compiler, 'experimental.mjs'), `export { minifySync } from ${JSON.stringify(compilerEntry)};`);
  const version = value => writeFileSync(join(compiler, 'package.json'), JSON.stringify({ name: 'rolldown', version: value,
    exports: { './experimental': './experimental.mjs', './package.json': './package.json' } }));
  const build = () => {
    const result = spawnSync(process.execPath, [builder, f.root], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    return f.manifest();
  };
  version('fixture-version-one');
  const first = build();
  const original = readFileSync(f.artifact('js/lq/index.js'));
  writeFileSync(printer, readFileSync(printer, 'utf8') + '\n// A printer implementation revision.\n');
  const second = build();
  version('fixture-version-two');
  const third = build();
  assert.equal(new Set([first.revision, second.revision, third.revision]).size, 3);
  assert.equal(new Set([first.recipeHash, second.recipeHash, third.recipeHash]).size, 3);
  assert.deepEqual(readFileSync(join(f.root, first.entries['js/lq/index.js'])), original);
  assert.deepEqual(readFileSync(f.artifact('js/lq/index.js')), original);
  assert.equal(f.manifest().sourceHashes['js/lq/index.js'], createHash('sha256').update(readFileSync(join(f.root, 'js/lq/index.js'))).digest('hex'));
  assert.equal(Object.keys(f.manifest().sourceHashes).length, Object.keys(f.manifest().entries).length);
});

test('production measures emitted eager graph with each real gzip sidecar; dynamic graph excluded', async t => {
  const f = fixture(t);
  f.write('js/lq/index.js', "import '../ui_overlay_motion.js'; export { seed } from './seed.js'; export const load = () => import('./lazy.js');");
  f.write('js/ui_overlay_motion.js', 'export const motion = true;');
  await buildStaticAssets(f.root);
  const source = await measureLqEntry({ staticRoot: f.root });
  const production = await measureLqEntry({ staticRoot: f.root, production: true });
  assert.equal(source.mode, 'source');
  assert.equal(production.mode, 'production');
  assert.equal(production.sourceHashesVerified, true);
  assert.deepEqual(production.modules.map(item => item.source).sort(), ['js/lq/index.js', 'js/lq/seed.js', 'js/ui_overlay_motion.js']);
  const gzip = production.modules.reduce((sum, item) => {
    const bytes = readFileSync(f.artifact(item.source));
    const sidecar = readFileSync(`${f.artifact(item.source)}.gz`);
    assert.deepEqual(gunzipSync(sidecar), bytes);
    assert.equal(item.gzip, sidecar.length);
    assert.equal(item.bytes, bytes.length);
    return sum + sidecar.length;
  }, 0);
  assert.equal(production.gzip, gzip);
  assert.notEqual(production.gzip, gzipSync(Buffer.concat(production.modules.map(item => readFileSync(f.artifact(item.source))))).length);
  assert.equal(production.budget, 18 * 1024);
});

test('production refuses stale source, added/deleted source, recipe and Vite graph changes', async t => {
  const f = fixture(t);
  for (const change of [
    () => f.write('js/lq/lazy.js', 'export const changeEvenOutsideEagerGraph = true;'),
    () => f.write('js/added-business.js', 'export const added = true;'),
    () => unlinkSync(join(f.root, 'js/added-business.js')),
  ]) {
    await buildStaticAssets(f.root); change();
    await assert.rejects(measureLqEntry({ staticRoot: f.root, production: true }), /Stale static manifest: source hashes changed/);
  }
  await buildStaticAssets(f.root);
  const manifest = f.manifest();
  f.write('assets/manifest.json', JSON.stringify({ ...manifest, recipeHash: 'old' }));
  await assert.rejects(measureLqEntry({ staticRoot: f.root, production: true }), /recipe\/compiler changed/);
  f.write('assets/manifest.json', JSON.stringify(manifest));
  f.write('dist/manifest.json', '{}');
  await assert.rejects(measureLqEntry({ staticRoot: f.root, production: true }), /graph inputs changed/);
});

test('production refuses missing/corrupt gzip and imports outside the current immutable graph', async t => {
  const f = fixture(t);
  await buildStaticAssets(f.root);
  const file = f.artifact('js/lq/index.js');
  const original = readFileSync(file);
  const compressed = readFileSync(`${file}.gz`);
  unlinkSync(`${file}.gz`);
  await assert.rejects(measureLqEntry({ staticRoot: f.root, production: true }), /ENOENT/);
  writeFileSync(`${file}.gz`, gzipSync('wrong artifact'));
  await assert.rejects(measureLqEntry({ staticRoot: f.root, production: true }), /Gzip sidecar differs/);
  const corrupt = Buffer.from("import '/static/js/lq/seed.js';");
  writeFileSync(file, corrupt); writeFileSync(`${file}.gz`, gzipSync(corrupt));
  await assert.rejects(measureLqEntry({ staticRoot: f.root, production: true }), /leaves current immutable graph/);
  writeFileSync(file, original); writeFileSync(`${file}.gz`, compressed);
});

test('CLI fails the production budget on a large eager child, not merely the tiny entry', async t => {
  const f = fixture(t);
  f.write('js/lq/seed.js', `export const seed = ${JSON.stringify(randomBytes(28_000).toString('base64'))};`);
  await buildStaticAssets(f.root);
  const result = spawnSync(process.execPath, ['tools/ui/measure_lq_entry.mjs', '--production', '--check', '--static-root', f.root], { cwd: repository, encoding: 'utf8' });
  assert.equal(result.status, 1, result.stderr);
  const report = JSON.parse(result.stdout);
  assert.equal(report.mode, 'production'); assert.equal(report.passed, false);
  assert.ok(report.gzip > report.budget);
  assert.ok(report.modules.find(item => item.source === 'js/lq/index.js').gzip < 256);
  assert.ok(report.modules.find(item => item.source === 'js/lq/seed.js').gzip > report.budget);
});

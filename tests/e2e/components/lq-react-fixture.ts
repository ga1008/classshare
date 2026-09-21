import path from 'node:path';

/** Compile real TSX in memory; no production outputs or app server. */
export async function buildReactFixture(source: string) {
  const { build } = await import('vite');
  const react = (await import('@vitejs/plugin-react')).default;
  const entry = '\0lq-react-fixture.tsx';
  const result = await build({
    configFile: false, logLevel: 'error', publicDir: false,
    define: { 'process.env.NODE_ENV': JSON.stringify('development') },
    resolve: { alias: { '@': path.resolve('frontend/src') } },
    plugins: [react(), { name: 'lq-react-fixture', enforce: 'pre',
      resolveId(id) { if (id.replaceAll('\\', '/').endsWith('/lq-react-fixture') || id === 'lq-react-fixture') return entry; },
      load(id) { if (id === entry) return source; },
    }],
    build: { write: false, minify: false, lib: { entry: 'lq-react-fixture', formats: ['es'] }, rolldownOptions: { output: { codeSplitting: false } } },
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap(item => 'output' in item ? item.output : []);
  const chunk = outputs.find(item => item.type === 'chunk' && item.isEntry);
  if (!chunk || chunk.type !== 'chunk') throw new Error('React fixture did not compile');
  return chunk.code;
}

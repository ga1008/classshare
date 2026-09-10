// ACP stdout belongs exclusively to DSH. This supervisor emits diagnostics to stderr.
import { stat } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createGatewayRelay } from './gateway-relay.mjs';

const socketPath = '/run/lanshare-agent/gateway.sock';
const relay = createGatewayRelay(socketPath);
let child;

async function main() {
  if (!(await stat(socketPath)).isSocket()) throw new Error('required gateway socket is unavailable');
  await new Promise((resolve, reject) => {
    relay.server.once('error', reject);
    relay.server.listen(8787, '127.0.0.1', resolve);
  });
  child = spawn(process.execPath,
    [fileURLToPath(new URL('./node_modules/@deepseek-ai/dsh/lib/bin.js', import.meta.url)), '--profile', 'lanshare'],
    { cwd: '/workspace', env: process.env, stdio: 'inherit' });
  let stopping = false;
  const stop = (signal) => {
    if (stopping) return;
    stopping = true;
    child.kill(signal);
    const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
    timer.unref();
  };
  process.on('SIGTERM', () => stop('SIGTERM'));
  process.on('SIGINT', () => stop('SIGINT'));
  const code = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (status) => resolve(status ?? 1));
  });
  await relay.close();
  process.exitCode = code;
}

main().catch(async () => {
  process.stderr.write('DSH runner startup or transport failed\n');
  child?.kill('SIGKILL');
  await relay.close();
  process.exitCode = 78;
});

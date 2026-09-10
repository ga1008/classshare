import assert from 'node:assert/strict';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import { createGatewayRelay } from '../../deployment/dsh/gateway-relay.mjs';

const socketPath = process.platform === 'win32'
  ? `\\\\.\\pipe\\lanshare-relay-test-${process.pid}`
  : path.join(os.tmpdir(), `lanshare-relay-test-${process.pid}.sock`);
const observed = [];
let streamClosed;
const streamEnded = new Promise((resolve) => { streamClosed = resolve; });
const upstream = http.createServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = Buffer.concat(chunks).toString();
  observed.push({ path: request.url, headers: request.headers, body });
  if (body === 'stream') {
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    response.write('data: first\n\n');
    response.once('close', streamClosed);
    return;
  }
  response.writeHead(200, { 'content-type': 'application/json', 'mcp-session-id': 'fixture-session',
    'set-cookie': 'must-not-forward', 'x-unsafe': 'must-not-forward' });
  response.end(JSON.stringify({ ok: true }));
});
upstream.listen(socketPath);
await once(upstream, 'listening');
const relay = createGatewayRelay(socketPath);
relay.server.listen(0, '127.0.0.1');
await once(relay.server, 'listening');
const port = relay.server.address().port;

function request(route, body, headers = {}, method = 'POST') {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: route, method, headers }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject);
    // Explicit two writes produce a chunked client request. The relay must
    // normalize it to one finite Content-Length request on the Unix socket.
    req.write(body.slice(0, 2));
    req.end(body.slice(2));
  });
}
try {
  const result = await request('/api/agent-model/messages', '{"x":1}', {
    'x-api-key': 'fixture-task-token', 'anthropic-version': '2023-06-01',
    'authorization': 'Bearer fixture-task-token', 'cookie': 'must-not-forward',
    'x-forwarded-for': 'must-not-forward', 'content-type': 'application/json',
  });
  assert.equal(result.status, 200);
  assert.equal(result.headers['mcp-session-id'], 'fixture-session');
  assert.equal(result.headers['set-cookie'], undefined);
  assert.equal(observed[0].headers['content-length'], '7');
  assert.equal(observed[0].headers['transfer-encoding'], undefined);
  assert.equal(observed[0].headers['x-api-key'], 'fixture-task-token');
  assert.equal(observed[0].headers['anthropic-version'], '2023-06-01');
  assert.equal(observed[0].headers.cookie, undefined);
  assert.equal(observed[0].headers['x-forwarded-for'], undefined);
  for (const route of ['/admin', '/api/agent-bridge/../admin', '/api/agent-bridge/%2e%2e/admin',
    '/api/agent-model/messages?next=http://host', '/api/agent-bridge/unregistered']) {
    assert.equal((await request(route, '{}')).status, 403);
  }
  assert.equal(observed.length, 1);
  const question = '/api/agent-bridge/questions/12345678-1234-4234-8234-123456789abc';
  for (const [route, method] of [['/api/agent-bridge/questions', 'POST'], [question, 'GET'], [question + '/cancel', 'POST']]) {
    assert.equal((await request(route, method === 'POST' ? '{}' : '', {}, method)).status, 200);
  }
  for (const [route, method] of [[question, 'POST'], [question + '/cancel', 'GET'], [question + '?key=x', 'GET'],
    ['/api/agent-bridge/questions/anything', 'GET'], [question + '/anything', 'POST']]) {
    assert.equal((await request(route, '', {}, method)).status, 403);
  }
  assert.equal(observed.length, 4);
  try {
    assert.equal((await request('/api/agent-bridge/mcp', 'x'.repeat(2 * 1024 * 1024 + 1))).status, 413);
  } catch (error) {
    // Node may reset an over-limit request still being uploaded.
    assert.equal(error.code, 'ECONNRESET');
  }
  assert.equal(observed.length, 4);
  await new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: '/api/agent-model/chat/completions', method: 'POST' }, (res) => {
      res.once('data', (data) => { assert.match(data.toString(), /data: first/); req.destroy(); resolve(); });
    });
    req.on('error', reject);
    req.end('stream');
  });
  await Promise.race([streamEnded, new Promise((_, reject) => setTimeout(() => reject(new Error('upstream not cancelled')), 2000).unref())]);
  process.stdout.write('relay framing, auth headers, routes, limits, SSE and disconnect: passed\n');
} finally {
  await relay.close();
  upstream.closeAllConnections();
  await new Promise((resolve) => upstream.close(resolve));
}

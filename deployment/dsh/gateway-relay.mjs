// No DNS or TCP upstream: all traffic goes through the launcher's fixed socket.
import http from 'node:http';

const allowed = new Map([
  ['/api/agent-model/chat/completions', ['POST']], ['/api/agent-model/messages', ['POST']],
  ['/api/agent-bridge/meta', ['GET']], ['/api/agent-bridge/schema', ['GET']],
  ['/api/agent-bridge/query', ['POST']], ['/api/agent-bridge/search', ['POST']],
  ['/api/agent-bridge/file', ['POST']], ['/api/agent-bridge/web', ['POST']],
  ['/api/agent-bridge/mcp', ['GET', 'POST', 'DELETE']],
  ['/api/agent-bridge/questions', ['POST']],
  ['/api/agent-bridge/children/admit', ['POST']],
]);
const questionPath = /^\/api\/agent-bridge\/questions\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\/cancel)?$/;
function permits(path, method) {
  if (allowed.get(path)?.includes(method)) return true;
  if (/^\/api\/agent-bridge\/children\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/finish$/.test(path)) return method === 'POST';
  const match = questionPath.exec(path);
  return !!match && method === (match[1] ? 'POST' : 'GET');
}
const requestHeaders = new Set(['authorization', 'x-api-key', 'content-type', 'accept',
  'anthropic-version', 'anthropic-beta', 'mcp-session-id', 'mcp-protocol-version', 'last-event-id']);
const maxBodyBytes = 2 * 1024 * 1024;

function fail(response, status) {
  if (response.destroyed) return;
  if (response.headersSent) return response.destroy();
  response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  response.end(JSON.stringify({ error: 'agent_gateway_unavailable_or_refused' }));
}

export function createGatewayRelay(socketPath) {
  // socketPath is injected only by the image-owned supervisor, never task input.
  const upstreams = new Set();
  let pending = 0;
  const server = http.createServer(async (request, response) => {
    if (!permits(request.url, request.method)) return fail(response, 403);
    if (pending >= 16) return fail(response, 503);
    pending += 1;
    let upstream;
    response.once('close', () => {
      pending -= 1;
      if (!response.writableEnded) upstream?.destroy();
    });
    try {
      // ACP/MCP/model inputs are finite JSON requests. Bound and normalize the
      // body so the Unix gateway never sees ambiguous or chunked HTTP framing.
      const chunks = [];
      let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > maxBodyBytes) {
          fail(response, 413);
          return;
        }
        chunks.push(chunk);
      }
      if (response.destroyed) return;
      const headers = { host: 'localhost', 'content-length': size };
      for (const [name, value] of Object.entries(request.headers)) {
        if (requestHeaders.has(name)) headers[name] = value;
      }
      upstream = http.request({ socketPath, path: request.url, method: request.method, headers, agent: false });
      upstreams.add(upstream);
      upstream.setTimeout(300_000, () => upstream.destroy(new Error('gateway timeout')));
      upstream.once('close', () => upstreams.delete(upstream));
      upstream.once('error', () => fail(response, 502));
      upstream.once('response', (result) => {
        if (response.destroyed) return result.destroy();
        const safe = {};
        for (const name of ['content-type', 'cache-control', 'content-length', 'mcp-session-id', 'retry-after']) {
          if (result.headers[name] !== undefined) safe[name] = result.headers[name];
        }
        response.writeHead(result.statusCode ?? 502, safe);
        result.once('error', () => response.destroy());
        result.pipe(response);
      });
      upstream.end(Buffer.concat(chunks, size));
    } catch {
      upstream?.destroy();
      fail(response, 400);
    }
  });
  server.requestTimeout = 30_000;
  server.headersTimeout = 15_000;
  server.on('clientError', (_error, socket) => socket.destroy());
  return {
    server,
    close: async () => {
      for (const request of upstreams) request.destroy();
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

const BASE = 'http://127.0.0.1:8787/api/agent-bridge/children';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function createLedger({ token, fetchImpl = fetch }) {
  if (!/^lsagt_[A-Za-z0-9_-]{32,128}$/.test(token ?? '')) throw new Error('Missing scoped workflow credential');
  async function call(path, body, signal) {
    const bounded = AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(8000)]);
    const response = await fetchImpl(BASE + path, { method: 'POST', signal: bounded, redirect: 'error',
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body) });
    if (!response.ok) { await response.body?.cancel(); throw new Error('Task child admission ledger refused the request'); }
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > 8192) throw new Error('Workflow ledger reply exceeds its limit');
        chunks.push(Buffer.from(value));
      }
      return JSON.parse(Buffer.concat(chunks, size).toString('utf8'));
    } finally { await reader.cancel().catch(() => {}); }
  }
  return {
    async admit(payload, signal) {
      const result = await call('/admit', payload, signal);
      if (!UUID.test(result?.id ?? '') || result.admitted !== true || result.request_id !== payload.request_id
          || result.child_session_id !== payload.child_session_id || result.total_limit !== 4)
        throw new Error('Invalid workflow child admission receipt');
      return result;
    },
    async finish(id, status) {
      if (!UUID.test(id) || !['completed', 'aborted', 'error'].includes(status)) throw new Error('Invalid child observation');
      const result = await call('/' + id + '/finish', { status }, AbortSignal.timeout(3000));
      if (result?.id !== id || result.runtime_reported_status !== status || result.host_execution_verified !== false)
        throw new Error('Invalid workflow child observation receipt');
      return result;
    },
  };
}

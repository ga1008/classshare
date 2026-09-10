import assert from 'node:assert/strict';
import { createAnswerer, apply } from '../../deployment/dsh/plugins/question-answerer/index.mjs';
const token = 'lsagt_' + 'b'.repeat(43);
const id = '12345678-1234-4234-8234-123456789abc';
const questions = [{ id: 'choice', question: 'Choose', options: [{ label: 'A' }, { label: 'B' }] }];
let requests, polls, scenario;
const fetchImpl = async (url, options) => {
  assert.ok(url.startsWith('http://127.0.0.1:8787/api/agent-bridge/questions'));
  assert.equal(options.headers.Authorization, 'Bearer ' + token);
  assert.equal(options.redirect, 'error');
  requests.push({ url, ...options });
  if (url.endsWith('/cancel')) return Response.json({ id, status: 'canceled' });
  if (options.method === 'POST') {
    const body = JSON.parse(options.body);
    assert.match(body.request_id, /^[0-9a-f-]{36}$/);
    assert.deepEqual(body.questions, questions);
    assert.equal(body.timeout_seconds, 300);
    return Response.json({ id, status: 'pending', expires_at: Date.now() / 1000 + 300 });
  }
  polls++;
  if (scenario === 'error') return new Response('', { status: 403 });
  if (scenario === 'expired') return Response.json({ id, status: 'expired' });
  if (scenario === 'invalid') return Response.json({ id, status: 'answered', answers: [{ id: 'choice', selected: ['unknown'] }] });
  return Response.json({ id, status: polls > 1 && scenario === 'answer' ? 'answered' : 'pending',
    answers: [{ id: 'choice', selected: [], custom: 'A custom answer' }] });
};
for (scenario of ['answer', 'error', 'expired', 'invalid', 'timeout', 'cancel']) {
  requests = []; polls = 0;
  const controller = new AbortController();
  const answerer = createAnswerer({ token, fetchImpl, pollMs: 5, timeoutMs: scenario === 'timeout' ? 20 : 500 });
  const pending = answerer({ questions, signal: controller.signal });
  if (scenario === 'cancel') setTimeout(() => controller.abort(), 15);
  if (scenario === 'answer') assert.deepEqual(await pending, { answers: [{ id: 'choice', selected: [], custom: 'A custom answer' }] });
  else {
    await assert.rejects(pending, { name: 'UserQuestionError', code: 'ASK_ABORTED' });
    assert.ok(requests.at(-1).url.endsWith('/cancel'));
    assert.equal(requests.at(-1).signal.aborted, false);
  }
}
assert.throws(() => createAnswerer({ token: 'real-or-missing-key' }), /task-scoped/);
const previous = process.env.DSH_BROKER_TOKEN;
try {
  delete process.env.DSH_BROKER_TOKEN;
  let published = false;
  assert.throws(() => apply({ on() {}, provide() { published = true; } }), /task-scoped/);
  assert.equal(published, false);
} finally {
  if (previous !== undefined) process.env.DSH_BROKER_TOKEN = previous;
}
process.stdout.write('question answerer: await, custom, refusal, expiry, invalid, timeout, cancel and startup passed\n');

// Image-owned adapter for the official userQuestions seam. A returned answer
// resumes the original tool; it never grants permission to a business write.
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';

export const name = 'lanshare-question-answerer';
export const inject = ['userQuestions'];
const baseURL = 'http://127.0.0.1:8787/api/agent-bridge/questions';
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function questionError(message, code = 'ASK_ABORTED') {
  // The official seam restores this documented error shape across plugins.
  return Object.assign(new Error(message), { name: 'UserQuestionError', code });
}

function acceptedAnswers(questions, answers) {
  if (!Array.isArray(answers) || answers.length !== questions.length) throw questionError('Invalid platform answer');
  const ids = new Set();
  return answers.map((answer) => {
    const question = questions.find((item) => item.id === answer?.id);
    if (!question || ids.has(answer.id) || !Array.isArray(answer.selected)) throw questionError('Invalid platform answer');
    ids.add(answer.id);
    const labels = new Set((question.options ?? []).map((option) => option.label));
    if (answer.selected.some((value) => !labels.has(value)) || new Set(answer.selected).size !== answer.selected.length
      || (!question.multiSelect && answer.selected.length > 1)
      || (answer.custom !== undefined && (typeof answer.custom !== 'string' || answer.custom.length > 4000))
      || (!question.multiSelect && answer.custom && answer.selected.length)) throw questionError('Invalid platform answer');
    return { id: answer.id, selected: [...answer.selected], ...(answer.custom !== undefined ? { custom: answer.custom } : {}) };
  });
}

export function createAnswerer({ token, fetchImpl = fetch, pollMs = 1000, timeoutMs = 300_000 } = {}) {
  if (!/^lsagt_[A-Za-z0-9_-]{32,128}$/.test(token ?? '')) throw new Error('Missing task-scoped question credential');
  async function call(path, method, body, signal) {
    const bounded = AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(8000)]);
    const response = await fetchImpl(baseURL + path, { method, signal: bounded, redirect: 'error',
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json', 'Content-Type': 'application/json' },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
    if (!response.ok) { await response.body?.cancel(); throw questionError('Platform question request was refused'); }
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > 32768) throw questionError('Platform answer exceeds its limit');
        chunks.push(Buffer.from(value));
      }
      return JSON.parse(Buffer.concat(chunks, size).toString('utf8'));
    } finally { await reader.cancel().catch(() => {}); }
  }
  return async (request) => {
    if (request.signal?.aborted) throw questionError('Question canceled');
    const deadline = AbortSignal.timeout(timeoutMs);
    const signal = AbortSignal.any([...(request.signal ? [request.signal] : []), deadline]);
    let identifier;
    let answered = false;
    try {
      const created = await call('', 'POST', { request_id: randomUUID(), questions: request.questions, timeout_seconds: 300 }, signal);
      if (!uuid.test(created?.id ?? '')) throw questionError('Invalid platform question reference');
      identifier = created.id;
      let state = created;
      for (;;) {
        if (state.id !== identifier) throw questionError('Platform question reference changed');
        if (state.status === 'answered') {
          const answers = acceptedAnswers(request.questions, state.answers);
          answered = true;
          return { answers };
        }
        if (state.status !== 'pending') throw questionError('Question expired or canceled');
        await delay(pollMs, undefined, { signal });
        state = await call('/' + identifier, 'GET', undefined, signal);
      }
    } catch (error) {
      if (error?.name === 'UserQuestionError') throw error;
      throw questionError('Question canceled or platform unavailable');
    } finally {
      // Independent short signal: the original run signal is already aborted.
      // Task termination also closes pending questions in the platform ledger.
      if (identifier && !answered) await call('/' + identifier + '/cancel', 'POST', {}, AbortSignal.timeout(2000)).catch(() => {});
    }
  };
}

export function apply(ctx) {
  const answer = createAnswerer({ token: process.env.DSH_BROKER_TOKEN });
  ctx.on('user-questions/request', (request) => answer(request));
  ctx.provide('lanshareQuestionAnswerer', { ready: true });
}

// node --test tests/test_agent_dsh_workflow.mjs
// Requires the explicitly installed, fixed official packages in dsh-poc.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { resolve, join } from 'node:path';
import { mkdtemp, cp, rm, readFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { AdmissionGate } from '../deployment/dsh/plugins/bounded-workflow/admission.mjs';

const root = resolve('.codex-temp/dsh-poc');
const require = createRequire(pathToFileURL(join(root, 'package.json')));
const load = name => import(pathToFileURL(require.resolve(name)).href);
assert.equal(JSON.parse(await readFile(join(root, 'node_modules/@deepseek-ai/dsh/package.json'))).version, '0.1.5-rc.1');
const { Context, Service } = await load('@deepseek-ai/cordis');
const { default: SystemPrompt } = await load('@deepseek-ai/dsh-system-prompt');
const { default: Tools } = await load('@deepseek-ai/dsh-tools');
const { createScope } = await load('@deepseek-ai/dsh-scope');
const directory = await mkdtemp(join(root, 'workflow-unit-'));
await cp(resolve('deployment/dsh/plugins/bounded-workflow'), directory, { recursive: true });
const { createBoundedProvider } = await import(pathToFileURL(join(directory, 'provider.mjs')));
const { attachToolIntersection, parentToolSnapshot } = await import(pathToFileURL(join(directory, 'scope.mjs')));
const { attachStructuredOutput } = await import(pathToFileURL(join(directory, 'structured.mjs')));
test.after(() => rm(directory, { recursive: true, force: true }));

function register(ctx, name, execute = async () => ({})) {
  ctx.tools.register({ name, description: 'Synthetic fixture', parameters: { type: 'object', properties: {} },
    output: { schema: { type: 'object' }, render: () => [{ type: 'text', text: 'fixture' }] }, execute });
}
async function fixture(body, { mountError = false, createDelay = 0, disposeDelay = 0, disposeError = false } = {}) {
  const ctx = new Context();
  const facts = { published: 0, disposed: 0, active: 0, peak: 0, admits: [], finishes: [], mounts: 0 };
  await ctx.plugin(SystemPrompt);
  await ctx.plugin(Tools);
  class Agents extends Service {
    constructor(c) { super(c, 'agents'); }
    async create(options) {
      const agent = { id: options.sessionId, options: options.agentOptions };
      const scope = createScope(this.ctx, agent);
      agent.ctx = scope.ctx;
      const events = [];
      agent.session = { header: { id: agent.id, ...options.meta }, requestHeader: () => undefined,
        append: (type, data) => events.push({ type, data }), snapshotEvents: () => events };
      let idle;
      agent.followup = () => { idle = delay(20).then(() => events.push({ type: 'step/start', data: { turn: 1 } },
        { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })); };
      agent.whenIdle = () => idle;
      agent.cancel = () => {};
      try {
        if (createDelay) await delay(createDelay, undefined, { signal: options.signal });
        await options.setup(agent.ctx, agent);
        options.signal.throwIfAborted();
      } catch (error) { await scope.dispose(); throw error; }
      facts.published++; facts.active++; facts.peak = Math.max(facts.peak, facts.active);
      return { agent, dispose: async () => {
        if (disposeDelay) await delay(disposeDelay);
        if (disposeError) throw new Error('Synthetic disposal failure');
        await idle; await scope.dispose(); facts.disposed++; facts.active--;
      } };
    }
  }
  Agents.inject = ['tools', 'systemPrompt'];
  await ctx.plugin(Agents);
  register(ctx, 'global_allowed');
  register(ctx, 'global_denied');
  register(ctx, 'workflow');
  try {
    await ctx.inject(['tools', 'systemPrompt', 'agents'], async c => {
      const parent = { id: '00000000-0000-4000-8000-000000000001', options: {},
        session: { header: { id: '00000000-0000-4000-8000-000000000001' }, requestHeader: () => undefined } };
      const scope = createScope(c, parent); parent.ctx = scope.ctx;
      register(parent.ctx, 'mcp__lanshare__echo');
      parent.ctx.tools.restrict({ deny: ['global_denied'] });
      const provider = createBoundedProvider({ model: 'fixture-model',
        mountMcp: async childCtx => { facts.mounts++; if (mountError) throw new Error('MCP unavailable');
          register(childCtx, 'mcp__lanshare__echo'); register(childCtx, 'mcp__lanshare__not_delegated'); },
        ledger: { admit: async payload => { facts.admits.push(payload); return { id: String(facts.admits.length) }; },
          finish: async (id, status) => { facts.finishes.push({ id, status }); } },
      });
      try { await body({ ctx, parent, provider, facts, createScope }); }
      finally { await provider.close().catch(() => {}); await scope.dispose(); }
    });
  } finally {
    for (const runtime of [...ctx.registry.values()].reverse()) for (const fiber of [...runtime.fibers]) await fiber.dispose();
  }
}
const request = (parent, signal = new AbortController().signal) => ({ parent, signal, prompt: [{ type: 'text', text: 'Synthetic child' }], descriptor: {} });

test('gate is shared, cancellation drains its queue and never refunds total', async () => {
  const gate = new AdmissionGate(), a = new AbortController(), b = new AbortController();
  const release = await gate.acquire(a.signal);
  const pending = gate.acquire(b.signal); b.abort(); await assert.rejects(pending);
  assert.deepEqual(gate.snapshot, { used: 2, active: 1, queued: 0, closed: false });
  release(); release();
  (await gate.acquire(a.signal))(); (await gate.acquire(a.signal))();
  await assert.rejects(gate.acquire(a.signal), /exhausted/);
});
test('official scopes: discovery and actual execution enforce parent intersection', async () => fixture(async ({ parent, provider, facts }) => {
  const run = await provider.start(request(parent));
  const child = run.localAgent;
  const assembly = await child.ctx.systemPrompt.assemble({ scope: child });
  assert.deepEqual(assembly.tools.map(tool => tool.name).sort(), ['global_allowed', 'mcp__lanshare__echo']);
  for (const name of ['workflow', 'global_denied', 'mcp__lanshare__not_delegated', 'structured_output']) {
    const result = await child.ctx.tools.execute({ callId: 'fixture-call', name, arguments: {}, agent: child, signal: new AbortController().signal });
    assert.equal(result.isError, true, name);
    assert.match(result.error.message, /scope|Unknown tool|not found/i);
  }
  assert.equal((await child.ctx.tools.execute({ callId: 'allowed', name: 'global_allowed', arguments: {}, agent: child,
    signal: new AbortController().signal })).isError, false);
  parent.ctx.tools.restrict({ deny: ['global_allowed'] });
  const result = await child.ctx.tools.execute({ callId: 'revoked', name: 'global_allowed', arguments: {}, agent: child, signal: new AbortController().signal });
  assert.equal(result.isError, true);
  assert.equal((await run.result).stopReason, 'completed');
  assert.equal(facts.finishes.length, 0);
  await run.dispose(); await run.dispose();
  assert.equal(facts.disposed, 1); assert.equal(facts.finishes.length, 1);
}));
test('two workflow callers share one slot including disposal, four total', async () => fixture(async ({ parent, provider, facts }) => {
  await Promise.all(Array.from({ length: 4 }, async () => {
    const run = await provider.start(request(parent)); await run.result; await run.dispose();
  }));
  assert.equal(facts.peak, 1); assert.equal(facts.disposed, 4);
  await assert.rejects(provider.start(request(parent)), /exhausted/);
}, { disposeDelay: 25 }));
test('depth and model/persona overrides reject before admission', async () => fixture(async ({ parent, provider, facts }) => {
  for (const extra of [{ agentOptions: { model: 'other' } }, { persona: 'changed' }, { toolFilter: { allow: ['workflow'] } }, { maxDepth: 0 }])
    await assert.rejects(provider.start({ ...request(parent), ...extra }));
  parent.session.header.delegationDepth = 1;
  await assert.rejects(provider.start(request(parent)), /depth/);
  assert.equal(facts.admits.length, 0);
}));
test('MCP failure rolls back unpublished child and settles runtime error', async () => fixture(async ({ parent, provider, facts }) => {
  await assert.rejects(provider.start(request(parent)), /MCP unavailable/);
  assert.equal(facts.published, 0); assert.equal(provider.snapshot.active, 0);
  assert.equal(facts.finishes[0].status, 'error');
}, { mountError: true }));
test('provider close drains startup and rejects queued work without publication', async () => fixture(async ({ parent, provider, facts }) => {
  const starts = [provider.start(request(parent)), provider.start(request(parent))];
  const results = Promise.allSettled(starts);
  await delay(10); await provider.close();
  assert.ok((await results).every(row => row.status === 'rejected'));
  assert.equal(facts.published, 0); assert.equal(provider.snapshot.active, 0);
}, { createDelay: 100 }));
test('disposal failure never releases slot or starts queued child', async () => fixture(async ({ parent, provider, facts }) => {
  const first = await provider.start(request(parent));
  const second = provider.start(request(parent));
  const rejected = assert.rejects(second);
  await first.result; await assert.rejects(first.dispose(), /could not be confirmed/);
  await rejected;
  assert.equal(facts.published, 1); assert.equal(provider.snapshot.active, 1);
  assert.equal(provider.snapshot.closed, true); assert.equal(facts.finishes.length, 0);
}, { disposeError: true }));
test('official tools/result commits structured result, invalid schema value cannot finish', async () => fixture(async ({ parent, ctx }) => {
  const child = {}, scope = createScope(parent.ctx, child); child.ctx = scope.ctx;
  const capture = attachStructuredOutput(child.ctx, { type: 'object', properties: { n: { type: 'integer' } }, required: ['n'], additionalProperties: false });
  const exec = args => child.ctx.tools.execute({ callId: 'structured', name: 'structured_output', arguments: args, agent: child, signal: new AbortController().signal });
  assert.equal((await exec({ n: 'bad' })).isError, true); assert.equal(capture(), undefined);
  const valid = await exec({ n: 3 }); assert.equal(valid.isError, false, JSON.stringify(valid)); assert.deepEqual(capture(), { value: { n: 3 } });
  assert.equal((await exec({ n: 4 })).isError, true); await scope.dispose();
}));
test('child shell guard refuses background mode before invoking its body', async () => fixture(async ({ parent }) => {
  let called = 0;
  register(parent.ctx, 'pwsh', async () => { called++; return {}; });
  const child = {}, scope = createScope(parent.ctx, child); child.ctx = scope.ctx;
  register(child.ctx, 'pwsh', async () => { called++; return {}; });
  attachToolIntersection(child.ctx, parent, new Set(['pwsh']), new AbortController().signal);
  const result = await child.ctx.tools.execute({ callId: 'background', name: 'pwsh', arguments: { run_in_background: true },
    agent: child, signal: new AbortController().signal });
  assert.equal(result.isError, true); assert.match(result.error.message, /background/); assert.equal(called, 0);
  await scope.dispose();
}));

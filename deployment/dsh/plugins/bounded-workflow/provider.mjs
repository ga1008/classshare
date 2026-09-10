import { randomUUID } from 'node:crypto';
import { foldConsumedWork } from '@deepseek-ai/dsh-agent';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { appendDelegatedPolicyOverrides, applyChildComposition, captureDelegatedPolicyOverrides,
  childSessionMeta, finalAssistantOutput, parentAgentOptionsForDelegation, resolveChildAgentOptions, resolveChildDepth } from '@deepseek-ai/dsh-subagent';
import { AdmissionGate } from './admission.mjs';
import { attachToolIntersection, parentToolSnapshot } from './scope.mjs';
import { attachStructuredOutput } from './structured.mjs';

const reasons = { completed: 'completed', 'max-tokens': 'max-tokens', aborted: 'aborted', blocked: 'refusal' };

export function createBoundedProvider({ model, mountMcp, ledger }) {
  if (!model || typeof mountMcp !== 'function' || typeof ledger?.admit !== 'function' || typeof ledger?.finish !== 'function')
    throw new Error('Workflow needs a fixed model, required MCP, and task admission ledger');
  const gate = new AdmissionGate();
  const runs = new Set();
  const pending = new Set();
  const controllers = new Set();
  const provider = {
    name: 'lanshare-bounded-spawn',
    inheritsParentContext: false,
    capabilities: { agentOptions: false, outputSchema: true, depthLimit: true, toolFilter: false, persona: false },
    get snapshot() { return gate.snapshot; },
    start(request) {
      const controller = new AbortController();
      controllers.add(controller);
      const operation = start(request, controller).finally(() => pending.delete(operation));
      pending.add(operation);
      return operation;
    },
    async close() {
      gate.close();
      for (const controller of controllers) controller.abort();
      await Promise.allSettled([...pending]);
      const results = await Promise.allSettled([...runs].map(dispose => dispose()));
      if (results.some(result => result.status === 'rejected')) throw new Error('Workflow children remain unconfirmed');
    },
  };
  async function start(request, controller) {
    let releasedToHolder = false;
    try {
      const { parent, signal } = request;
      signal.throwIfAborted();
      // Defend provider-direct callers as well as the official validated seam.
      if (request.agentOptions !== undefined || request.toolFilter !== undefined || request.persona !== undefined)
        throw new Error('Workflow child cannot select model, persona, or broader tools');
      const depth = resolveChildDepth(parent, 1);
      if (request.maxDepth !== undefined && (!Number.isSafeInteger(request.maxDepth) || request.maxDepth < depth))
        throw new Error('Workflow child exceeds its requested depth');
      const inherited = captureDelegatedPolicyOverrides(parent);
      const parentOptions = parentAgentOptionsForDelegation(parent);
      const maxTokens = Math.min(parentOptions.maxTokens ?? 16384, 16384);
      const combined = AbortSignal.any([signal, controller.signal]);
      const allowed = await parentToolSnapshot(parent, combined);
      if (![...allowed].some(name => name.startsWith('mcp__lanshare__')))
        throw new Error('Parent has no ready LanShare MCP tools');
      const release = await gate.acquire(combined);
      const childId = randomUUID();
      let admission, handle, readStructured, disposal, outcome = 'error';
      let result;
      const dispose = () => disposal ??= (async () => {
        controller.abort();
        if (handle) {
          const settled = await Promise.allSettled([handle.dispose(), result]);
          if (settled[0].status === 'rejected') { gate.close(); throw new Error('Workflow child disposal could not be confirmed'); }
        }
        // This is explicitly a runtime report, never host verified capacity.
        if (admission) await ledger.finish(admission.id, outcome).catch(() => { gate.close(); });
        runs.delete(dispose);
        controllers.delete(controller);
        release();
      })();
      try {
        combined.throwIfAborted();
        admission = await ledger.admit({ request_id: randomUUID(), parent_session_id: parent.id, child_session_id: childId, depth }, combined);
        combined.throwIfAborted();
        handle = await parent.ctx.agents.create({ sessionId: childId, parentAgent: parent,
          meta: childSessionMeta(parent, depth, false), signal: combined,
          agentOptions: resolveChildAgentOptions(parent, { provider: 'deepseek-official', model, maxTokens }, depth),
          setup: async (ctx, child) => {
            appendDelegatedPolicyOverrides(child.session, inherited);
            applyChildComposition(ctx, parent, {});
            attachToolIntersection(ctx, parent, allowed, combined, request.outputSchema !== undefined);
            if (request.outputSchema !== undefined) readStructured = attachStructuredOutput(ctx, request.outputSchema);
            await mountMcp(ctx, combined);
            combined.throwIfAborted();
            if (![...allowed].some(name => name.startsWith('mcp__lanshare__') && ctx.tools.get(name, child)))
              throw new Error('Child MCP did not discover the delegated platform tools');
            let appended = false;
            ctx.on('agent/pre-step', async ({ agent }, next) => {
              const decision = await next();
              if (!appended && decision.kind === 'enter') {
                appended = true;
                agent.session.append('subagent/descriptor', request.descriptor);
              }
              return decision;
            });
          },
        });
        const child = handle.agent;
        const abort = () => child.cancel({ kind: 'parent' });
        combined.addEventListener('abort', abort, { once: true });
        runs.add(dispose);
        result = (async () => {
          try {
            if (!combined.aborted) {
              child.followup(createUserMessage({ content: request.prompt, source: { kind: 'user' } }));
              await child.whenIdle();
            } else abort();
            const events = child.session.snapshotEvents(0);
            const reason = foldConsumedWork(events).end?.data.reason?.kind;
            let stopReason = reasons[reason] ?? 'error';
            if (combined.aborted && stopReason !== 'completed') stopReason = 'aborted';
            const structured = readStructured?.();
            if (readStructured && !structured && stopReason === 'completed') stopReason = 'error';
            outcome = ['completed', 'aborted'].includes(stopReason) ? stopReason : 'error';
            return { output: finalAssistantOutput(events) ?? [], stopReason,
              ...(structured ? { structured: structured.value } : {}) };
          } finally { combined.removeEventListener('abort', abort); }
        })();
        // Keep the signal-to-child link until the authoritative turn settles.
        releasedToHolder = true;
        return { id: childId, localAgent: child, result, dispose };
      } catch (error) {
        outcome = combined.aborted ? 'aborted' : 'error';
        await dispose();
        throw error;
      }
    } finally {
      if (!releasedToHolder) controllers.delete(controller);
    }
  }
  return provider;
}

import * as McpClient from '@deepseek-ai/dsh-mcp-client';
import { createBoundedProvider } from './provider.mjs';
import { createLedger } from './ledger.mjs';

export const name = 'lanshare-bounded-workflow';
export const inject = ['subagents', 'agents', 'tools', 'systemPrompt'];

// Exported factory permits synthetic tests without making endpoints configurable
// in the production profile or accepting model-authored callback code.
export function install(ctx, options) {
  const provider = createBoundedProvider(options);
  ctx.subagents.registerProvider(provider);
  ctx.effect(() => () => provider.close(), 'lanshare.workflow.children');
  ctx.provide('lanshareBoundedWorkflow', { ready: true, provider: provider.name });
  return provider;
}

export function apply(ctx) {
  const token = process.env.DSH_BROKER_TOKEN;
  const model = process.env.DSH_GATEWAY_MODEL;
  const url = 'http://127.0.0.1:8787/api/agent-bridge/mcp';
  if (process.env.DSH_BROKER_MCP_URL !== url) throw new Error('Workflow MCP endpoint must use the fixed task relay');
  const ledger = createLedger({ token });
  return install(ctx, { model, ledger, mountMcp: async (childCtx, signal) => {
    signal.throwIfAborted();
    await childCtx.plugin(McpClient, { transport: 'streamable-http', serverName: 'lanshare', url,
      headers: { Authorization: `Bearer ${token}` }, toolCallTimeoutMs: 30_000,
      failOnStartupError: true, reconnect: { enabled: false } });
    signal.throwIfAborted();
  } });
}

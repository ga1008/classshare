// Public DSH seams only. A scope's own MCP registrations bypass restrict(),
// so model presentation and the monotonic execution guard enforce the same set.
const DELEGATION = new Set(['workflow', 'subagent', 'subagent_fork', 'send_message', 'interrupt_agent', 'list_agents', 'ralph']);
const BACKGROUND = new Set(['run_code', 'cordis', 'jobs', 'pty', 'start_process', 'create_goal', 'get_goal', 'update_goal']);
export function delegable(name) {
  return !DELEGATION.has(name) && !BACKGROUND.has(name) && !name.startsWith('job_');
}

export async function parentToolSnapshot(parent, signal) {
  const assembly = await parent.ctx.systemPrompt.assemble({ scope: parent, signal });
  signal.throwIfAborted();
  return new Set(assembly.tools.map(tool => tool.name).filter(delegable));
}

export function attachToolIntersection(ctx, parent, allowed, signal, structured = false) {
  const permitted = (name) => !signal.aborted && allowed.has(name) && delegable(name)
    && parent.ctx.tools.get(name, parent) !== undefined;
  const terminal = name => structured && !signal.aborted && name === 'structured_output';
  ctx.tools.guard(exec => {
    if (['bash', 'pwsh'].includes(exec.name) && exec.arguments?.run_in_background === true)
      return 'Workflow children cannot start background shell jobs';
    return permitted(exec.name) || terminal(exec.name) ? undefined : 'Tool is outside the parent workflow delegation scope';
  });
  ctx.on('system-prompt/assemble', async (_assembly, _context, next) => {
    const result = await next();
    return { ...result, tools: result.tools.filter(tool => permitted(tool.name) || terminal(tool.name)) };
  });
  return permitted;
}

import { ToolArgsError, validateJsonSchemaValue } from '@deepseek-ai/dsh-tools';

// A value is recorded only by the official authoritative tools/result event.
// This provider keeps native tool mode; run_code is not admitted to children.
export function attachStructuredOutput(ctx, schema) {
  const staged = new WeakMap();
  let recorded;
  ctx.tools.register({
    name: 'structured_output', description: 'Return the final workflow child result exactly once.', parameters: schema,
    output: { schema: { type: 'object', properties: { recorded: { type: 'boolean' } }, required: ['recorded'], additionalProperties: false },
      render: () => [{ type: 'text', text: 'Structured workflow result recorded.' }] },
    async execute(args, exec) {
      const errors = validateJsonSchemaValue(schema, args);
      if (errors.length) throw new ToolArgsError(errors);
      if (exec.parent !== undefined) throw new Error('Nested structured output is unavailable');
      staged.set(exec, structuredClone(args));
      exec.concludeTurn();
      return { recorded: true };
    },
  });
  ctx.systemPrompt.context({ name: 'lanshare:workflow-result', order: 9000,
    text: 'When finished, call structured_output with the requested schema. Plain text is not a successful structured result.' });
  ctx.tools.guard(() => recorded !== undefined ? 'Workflow result is already final' : undefined);
  ctx.on('tools/result', (exec, result) => {
    if (!staged.has(exec)) return;
    const value = staged.get(exec);
    staged.delete(exec);
    if (!result.isError && recorded === undefined) recorded = { value };
  });
  return () => recorded;
}

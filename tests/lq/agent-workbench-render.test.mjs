import { describe, expect, it } from 'vitest';
import { renderLiveState } from '../../static/js/agent_workbench_render.js';

// Production task 19 was parked by the turn budget: the footer said 点“继续”
// but the only button lived in the task head, scrolled out of view.
describe('Agent workbench live row', () => {
    it('carries the resume action on a parked (paused) task', () => {
        const row = renderLiveState({ status: 'queued', runtime_status: 'paused', is_parked: true, is_terminal: false });
        expect(row.kind).toBe('live');
        expect(row.html).toContain('data-awb-action="resume"');
        expect(row.html).toContain('继续');
    });

    it('does not offer resume while waiting for an answer or while running', () => {
        const waiting = renderLiveState({ status: 'queued', runtime_status: 'waiting_input', is_parked: true, is_terminal: false });
        expect(waiting.html).not.toContain('data-awb-action="resume"');
        const running = renderLiveState({ status: 'running', runtime_status: 'running', is_parked: false, is_terminal: false, elapsed_seconds: 12 });
        expect(running.html).not.toContain('data-awb-action="resume"');
        expect(renderLiveState({ status: 'completed', is_terminal: true })).toBeNull();
    });
});

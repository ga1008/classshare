// 知识树生命周期命令：AI 生成/重新生成（确认会清空的进度与手动修改）、生成中轮询、
// 全部完成/全部取消。状态仍由控制器持有，经 ctx 读写。
import { showToast } from '/static/js/ui.js';
import { confirm as lqConfirm } from '../lq/dialogs.js';
import { progressFor } from './model.js';

const POLL_MS = 3000;
const POLL_LIMIT_MS = 22 * 60 * 1000; // server expires a stuck run after 20 min

/**
 * ctx: { data(), index(), canEdit, closed(), editingId(), saveEdit({quiet}), generateBtn,
 *        request(path, options), applyState(state), refresh(), mutate(path, options, optimistic) }
 */
export function createProgressCommands(ctx) {
    let pollTimer = 0;
    let pollStarted = 0;
    const generating = () => ctx.data()?.status === 'generating';

    function schedulePoll() {
        window.clearTimeout(pollTimer);
        if (ctx.closed() || !generating()) return;
        if (!pollStarted) pollStarted = Date.now();
        if (Date.now() - pollStarted > POLL_LIMIT_MS) return;
        pollTimer = window.setTimeout(async () => {
            await ctx.refresh();
            if (ctx.closed()) return;
            if (!generating()) {
                pollStarted = 0;
                const data = ctx.data();
                if (data?.error) showToast(data.error, 'error');
                else if (data?.has_tree) showToast('知识树已生成，请勾选已讲完的基础单元。', 'success');
                return;
            }
            schedulePoll();
        }, POLL_MS);
    }

    async function generate() {
        const data = ctx.data();
        if (!ctx.canEdit || !data || generating() || !data.has_material || !ctx.canGenerate()) return;
        if (data.has_tree) {
            const hasProgress = Number(data.completed_count) > 0;
            const loses = [hasProgress ? `当前已完成的 ${data.progress || 0}% 进度` : '', data.tree?.edited_at ? '手动编辑过的内容' : ''].filter(Boolean).join('和');
            const ok = await lqConfirm({
                title: '重新生成知识树？',
                message: loses ? `将用 AI 重新生成整棵知识树，并清空${loses}；生成后请按新知识树重新勾选。` : '将用 AI 重新生成并替换当前知识树。',
                confirmLabel: '重新生成',
                danger: Boolean(loses),
            });
            if (!ok || ctx.closed()) return;
        }
        if (!ctx.canGenerate()) return;
        if (ctx.editingId() && !await ctx.saveEdit({ quiet: true })) return;
        ctx.setGenerationPending(true);
        let failed = false;
        try {
            ctx.applyState(await ctx.request('/generate', { method: 'POST' }));
            pollStarted = Date.now();
            schedulePoll();
        } catch (error) {
            failed = true;
            showToast(error.message || '无法开始生成，请稍后重试。', 'error');
        } finally {
            ctx.setGenerationPending(false);
            if (failed && !ctx.closed()) {
                await ctx.refresh();
                // A lost POST response may still have started the server job.
                schedulePoll();
            }
        }
    }

    async function bulk(done) {
        const data = ctx.data();
        if (!data?.has_tree || !ctx.editable()) return;
        if (!done && Number(data.completed_count)) {
            const ok = await lqConfirm({ title: '全部取消？', message: '将清空本课所有已完成标记，进度归零。', confirmLabel: '全部取消', danger: true });
            if (!ok || ctx.closed()) return;
        }
        if (!ctx.editable()) return;
        const ids = done ? [...ctx.index().map.values()].filter(info => info.isLeaf).map(info => info.node.id) : [];
        ctx.mutate('/bulk', { method: 'POST', body: { done } }, state => progressFor(state.tree, ids));
    }

    return { generate, bulk, schedulePoll, stop() { window.clearTimeout(pollTimer); } };
}

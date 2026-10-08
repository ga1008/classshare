/* Run: node --test tests/frontend/materials_repository_bindings.test.cjs
 * Exercise the real Git action/finally and binding renderer together. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/materials_manage.js'), 'utf8');
const functions = [
    'getReadmeCandidateId', 'getReadmeCandidatePath', 'renderRepositoryAutoBindAssignments',
    'renderRepositoryAutoBindPanel', 'setRepositoryAutoBindBusy', 'setRepositoryBusy',
    'executeRepositoryAction', 'runRepositoryAutoBind',
].map((name) => {
    const match = source.match(new RegExp(`^(?:async )?function ${name}\\([\\s\\S]*?^}`, 'm'));
    assert.ok(match, `Production function ${name} exists`);
    return match[0];
}).join('\n');

function fixture(candidates) {
    const refs = Object.fromEntries([
        'repositoryStatus', 'repositoryUpdateBtn', 'repositoryPushBtn', 'repositoryCommandRunBtn',
        'repositoryAuthBtn', 'repositoryCredentialSaveBtn', 'repositoryCommandInput',
        'repositoryAutoBindPanel', 'repositoryAutoBindList', 'repositoryAutoBindSummary',
        'repositoryAutoBindRunBtn', 'repositoryAutoBindDismissBtn',
    ].map((key) => [key, { disabled: false, hidden: false, textContent: '', innerHTML: '' }]));
    const state = { repository: {
        materialId: 100, requestId: 0, busy: false, autoBindBusy: false,
        autoBindCandidates: [], autoBindResult: null,
        detail: { can_update: true, can_commit_push: true, credential_supported: true },
    } };
    const calls = [];
    const success = { message: '已按课次绑定 1 个文档', assignments: [{
        source: 'repository_ordinal', order_index: 3, material_path: 'lesson_3/lesson_3.html',
        course_name: 'Python', class_name: '人工智能',
    }] };
    const context = vm.createContext({
        state, refs, console,
        escapeHtml: (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
        formatRepositorySyncSummary: () => '更新 2',
        showToast() {},
        renderRepositoryModal: () => context.setRepositoryBusy(state.repository.busy),
        refreshRepositoryAffectedViews: async () => {},
        refreshRepositoryState: async () => {},
        apiFetch: async (url, options) => {
            calls.push({ url, options });
            assert.equal(state.repository.busy || state.repository.autoBindBusy, true);
            if (!refs.repositoryAutoBindPanel.hidden) {
                assert.equal(refs.repositoryAutoBindRunBtn.disabled, true, 'visible button is disabled during either request');
            }
            if (url.endsWith('/command')) return {
                status: 'success', learning_bindings: success, readme_candidates: candidates,
            };
            assert.ok(url.endsWith('/auto-bind-readmes'));
            return { message: 'AI 绑定已完成', assignments: [] };
        },
    });
    vm.runInContext(functions, context);
    return { context, state, refs, calls };
}

test('partially bound Git update leaves unresolved candidates actionable after finally', async () => {
    const { context, state, refs, calls } = fixture([{ material_id: 104, relative_path: 'unknown/README.md' }]);
    await context.executeRepositoryAction('update');
    assert.equal(state.repository.busy, false);
    assert.equal(refs.repositoryAutoBindPanel.hidden, false);
    assert.equal(refs.repositoryAutoBindRunBtn.disabled, false);
    assert.equal(refs.repositoryAutoBindDismissBtn.hidden, false);
    assert.match(refs.repositoryAutoBindSummary.textContent, /已按课次绑定 1 个文档.*1 个入口文档/);
    assert.match(refs.repositoryAutoBindList.innerHTML, /unknown\/README.md/);
    assert.equal(refs.repositoryUpdateBtn.disabled, false, 'other repository controls retain their normal capability rules');

    await context.runRepositoryAutoBind();
    assert.equal(calls.length, 2);
    assert.deepEqual(Array.from(calls[1].options.body.candidate_material_ids), [104]);
    assert.equal(refs.repositoryAutoBindRunBtn.disabled, true);
    assert.equal(refs.repositoryAutoBindDismissBtn.hidden, true);
    assert.match(refs.repositoryAutoBindSummary.textContent, /AI 绑定已完成/);
});

test('fully resolved Git update displays results and keeps AI unavailable after finally', async () => {
    const { context, refs } = fixture([]);
    await context.executeRepositoryAction('update');
    assert.equal(refs.repositoryAutoBindRunBtn.disabled, true);
    assert.equal(refs.repositoryAutoBindDismissBtn.hidden, true);
    assert.match(refs.repositoryAutoBindList.innerHTML, /lesson_3\/lesson_3.html/);
    assert.match(refs.repositoryAutoBindList.innerHTML, /按课次/);
});

test('invalid candidate ids cannot enable the fallback through a second busy-state owner', async () => {
    const { context, refs } = fixture([{ material_id: 0, relative_path: 'invalid/README.md' }]);
    await context.executeRepositoryAction('update');
    assert.equal(refs.repositoryAutoBindRunBtn.disabled, true);
    assert.match(refs.repositoryAutoBindList.innerHTML, /lesson_3\/lesson_3.html/);
});

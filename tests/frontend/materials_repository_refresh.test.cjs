/* Run: node --test tests/frontend/materials_repository_refresh.test.cjs
 * Execute the production refresh/selection functions against delayed material APIs. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/materials_manage.js'), 'utf8');
const functions = [
    'findTreeNode', 'findTreePath', 'syncWorkspaceSelection', 'resetMaterialWorkspace',
    'resetWorkspaceContent', 'resetWorkspaceContentForDetail', 'loadMaterialTree',
    'loadMaterialDetail', 'loadWorkspaceContent', 'openMaterialDetail', 'loadLibrary',
    'refreshRepositoryAffectedViews', 'executeRepositoryAction', 'openRepositoryModal',
    'renderFolderChildCards',
].map((name) => {
    const match = source.match(new RegExp(`^(?:async )?function ${name}\\([\\s\\S]*?^}`, 'm'));
    assert.ok(match, `Production function ${name} exists`);
    return match[0];
}).join('\n');

const copy = (value) => JSON.parse(JSON.stringify(value));
const file = (id, name, root = 100) => ({ id, name, root_id: root, node_type: 'file', editable: true, preview_type: 'text', children: [] });
const folder = (id, name, children = [], root = 100) => ({ id, name, root_id: root, node_type: 'folder', children });
const oldTree = () => folder(100, 'python-course', [folder(101, 'lesson_1', [file(102, 'lesson_1.html')])]);
const freshTree = () => folder(100, 'python-course', [
    folder(101, 'lesson_1', [file(102, 'lesson_1.html')]),
    folder(103, 'lesson_3', [file(104, 'lesson_3.html')]),
    folder(105, 'lesson_4', [file(106, 'lesson_4.html')]),
]);
const otherTree = () => folder(200, 'cnet-course', [file(201, 'main.html', 200)], 200);
const deferred = () => {
    let resolve;
    let reject;
    const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
    return { promise, resolve, reject };
};

function fixture({ tree = freshTree(), selected = 102, workspace = oldTree(), intercept } = {}) {
    const state = {
        currentParentId: null, libraryRequestId: 0, history: [], items: [], selectedIds: new Set(),
        activeMaterialId: selected, activeDetail: file(selected, 'old preview'), detailRequestId: 0,
        filters: {}, repository: { materialId: 100, requestId: 0, detail: { name: 'python-course' }, busy: false },
        materialWorkspace: {
            root: copy(workspace), stats: {}, selectedId: selected, expandedIds: new Set([100, 101, 999]),
            treeRequestId: 0, treeLoading: false,
            content: { materialId: selected, requestId: 0, text: 'OLD CONTENT' },
        },
    };
    const calls = [];
    const context = vm.createContext({
        state, Set, URLSearchParams, console,
        refs: { repositoryCommandInput: { focus() {} } },
        apiFetch: async (url, options = {}) => {
            calls.push(url);
            const intercepted = intercept?.(url, options);
            if (intercepted !== undefined) return await intercepted;
            if (url.endsWith('/repository/command')) return {
                status: 'success', repository: { name: 'python-course' }, combined_output: 'Already up to date.',
                readme_candidates: [], sync_summary: { inserted: 0, updated: 0, deleted: 0, unchanged: 243 },
            };
            if (url.endsWith('/repository')) return { repository: { name: url.includes('/200/') ? 'cnet-course' : 'python-course' } };
            if (url.endsWith('/tree')) return { tree: copy(url.includes('/200/') ? otherTree() : tree), stats: { file_count: 3 } };
            if (url.includes('/library')) return { items: [copy(tree), otherTree()], filters: {} };
            if (url.endsWith('/content')) return { content: 'NEW CONTENT', material: {} };
            const id = Number(url.split('/').pop());
            const node = context.findTreeNode(id, tree) || context.findTreeNode(id, otherTree());
            if (!node) throw Object.assign(new Error('Material no longer exists'), { status: 404 });
            return { material: copy(node) };
        },
        buildLibraryQuery: (id) => id ? `parent_id=${id}` : '',
        isDetailModalOpen: () => true,
        getVisualMeta: () => ({ color: '#345', label: 'HTML' }),
        escapeHtml: (text) => String(text), formatSize: () => '1 KB',
        setRepositoryBusy: (busy) => { state.repository.busy = busy; },
        formatRepositorySyncSummary: () => '新增 0 / 更新 0',
    });
    for (const name of [
        'renderDetail', 'renderList', 'renderStats', 'renderBreadcrumbs', 'renderNavigationState',
        'renderRepositoryToolbar', 'updateFilterControls', 'syncLibraryUrl', 'closeDetailModal',
        'openDetailModal', 'renderRepositoryModal', 'renderRepositoryAutoBindPanel', 'showToast',
        'openRepositoryCredentialModal', 'openModal',
    ]) context[name] = () => {};
    for (const name of [
        'normalizeKeyword', 'normalizeDocumentTypeFilter', 'normalizeScopeFilter', 'normalizeSortBy', 'normalizeSortOrder',
    ]) context[name] = (value) => value;
    context.refreshAiImportTasksForCurrentFolder = async () => {};
    vm.runInContext(functions, context);
    return { context, state, calls };
}

test('Already up to date refreshes the open tree, folder cards, and active file content', async () => {
    const { context, state, calls } = fixture();
    await context.executeRepositoryAction('update');
    assert.ok(calls.includes('/api/materials/100/tree'));
    assert.match(context.renderFolderChildCards({ id: 100 }), /lesson_3/);
    assert.match(context.renderFolderChildCards({ id: 100 }), /lesson_4/);
    assert.equal(state.activeMaterialId, 102);
    assert.equal(state.materialWorkspace.selectedId, 102);
    assert.deepEqual([...state.materialWorkspace.expandedIds], [100, 101]);
    assert.equal(state.materialWorkspace.content.text, 'NEW CONTENT');
    assert.equal(state.repository.lastOutput, 'Already up to date.');
});

test('a deleted selected file falls back to its surviving parent', async () => {
    const { context, state, calls } = fixture({ tree: folder(100, 'python-course', [folder(101, 'lesson_1')]) });
    await context.refreshRepositoryAffectedViews();
    assert.equal(state.activeMaterialId, 101);
    assert.equal(state.activeDetail.id, 101);
    assert.ok(!calls.includes('/api/materials/102'));
    assert.equal(state.materialWorkspace.content.text, '');
});

test('a deleted selected subtree falls back to the repository root', async () => {
    const { context, state } = fixture({ tree: folder(100, 'python-course') });
    await context.refreshRepositoryAffectedViews();
    assert.equal(state.activeMaterialId, 100);
    assert.equal(state.activeDetail.id, 100);
    assert.deepEqual([...state.materialWorkspace.expandedIds], [100]);
});

test('refreshing one repository leaves another open workspace and preview untouched', async () => {
    const { context, state, calls } = fixture({ selected: 201, workspace: otherTree() });
    await context.refreshRepositoryAffectedViews(100);
    assert.equal(state.materialWorkspace.root.id, 200);
    assert.equal(state.activeMaterialId, 201);
    assert.equal(state.materialWorkspace.content.text, 'OLD CONTENT');
    assert.ok(calls.every((url) => url.includes('/library')));
});

test('a late tree response cannot replace a workspace opened while refreshing', async () => {
    const pending = deferred();
    const { context, state } = fixture({ intercept: (url) => url === '/api/materials/100/tree' ? pending.promise : undefined });
    const refresh = context.refreshRepositoryAffectedViews(100);
    await context.openMaterialDetail(200);
    pending.resolve({ tree: freshTree(), stats: {} });
    await refresh;
    assert.equal(state.materialWorkspace.root.id, 200);
    assert.equal(state.activeDetail.id, 200);
    assert.equal(state.activeMaterialId, 200);
});

test('selecting another file during tree refresh preserves the newer selection', async () => {
    const pending = deferred();
    const { context, state } = fixture({ intercept: (url) => url === '/api/materials/100/tree' ? pending.promise : undefined });
    const refresh = context.refreshRepositoryAffectedViews(100);
    await context.loadMaterialDetail(101);
    pending.resolve({ tree: freshTree(), stats: {} });
    await refresh;
    assert.equal(state.materialWorkspace.selectedId, 101);
    assert.equal(state.activeDetail.id, 101);
});

test('a late preview response cannot replace a newly opened workspace', async () => {
    const pending = deferred();
    const requested = deferred();
    const { context, state } = fixture({ intercept: (url) => {
        if (url !== '/api/materials/102') return undefined;
        requested.resolve();
        return pending.promise;
    } });
    const refresh = context.refreshRepositoryAffectedViews(100);
    await requested.promise;
    await context.openMaterialDetail(200);
    pending.resolve({ material: file(102, 'old delayed response') });
    await refresh;
    assert.equal(state.materialWorkspace.root.id, 200);
    assert.equal(state.activeDetail.id, 200);
    assert.equal(state.materialWorkspace.content.materialId, null);
});

test('an old Git response cannot populate a different repository modal', async () => {
    const pending = deferred();
    const { context, state, calls } = fixture({ intercept: (url) => url.endsWith('/command') ? pending.promise : undefined });
    const update = context.executeRepositoryAction('update');
    await context.openRepositoryModal(200);
    pending.resolve({ status: 'success', repository: { name: 'old python' }, combined_output: 'old result' });
    await update;
    assert.equal(state.repository.materialId, 200);
    assert.equal(state.repository.detail.name, 'cnet-course');
    assert.equal(state.repository.lastOutput, '暂无输出');
    assert.equal(state.repository.busy, false);
    assert.ok(!calls.some((url) => url.endsWith('/tree')));
});

test('a late background library response cannot undo newer folder navigation', async () => {
    const pending = deferred();
    const { context, state } = fixture({ selected: 201, workspace: otherTree(), intercept: (url) => url === '/api/materials/library' ? pending.promise : undefined });
    const refresh = context.refreshRepositoryAffectedViews(100);
    await context.loadLibrary(200, true);
    pending.resolve({ items: [], filters: {} });
    await refresh;
    assert.equal(state.currentParentId, 200);
    assert.equal(state.activeMaterialId, 201);
    assert.equal(state.materialWorkspace.root.id, 200);
});

test('a deleted current library folder falls back to the surviving ancestor', async () => {
    const { context, state, calls } = fixture({ tree: folder(100, 'python-course'), intercept: (url) => {
        if (url !== '/api/materials/library?parent_id=101') return undefined;
        return Promise.reject(Object.assign(new Error('Folder was deleted'), { status: 404 }));
    } });
    state.currentParentId = 101;
    await context.refreshRepositoryAffectedViews(100);
    assert.equal(state.currentParentId, 100);
    assert.equal(state.activeMaterialId, 100);
    assert.ok(calls.includes('/api/materials/library?parent_id=100'));
});

test('an obsolete folder error cannot start fallback navigation over a newer request', async () => {
    const oldRequest = deferred();
    const newRequest = deferred();
    const { context, state, calls } = fixture({ selected: 201, workspace: otherTree(), intercept: (url) => {
        if (url === '/api/materials/library') return oldRequest.promise;
        if (url === '/api/materials/library?parent_id=200') return newRequest.promise;
        return undefined;
    } });
    const refresh = context.refreshRepositoryAffectedViews(100);
    const navigation = context.loadLibrary(200, true);
    oldRequest.reject(Object.assign(new Error('Obsolete folder error'), { status: 404 }));
    await refresh;
    newRequest.resolve({ items: [otherTree()], filters: {} });
    await navigation;
    assert.equal(state.currentParentId, 200);
    assert.equal(calls.filter((url) => url.includes('/library')).length, 2);
});

test('resetting a workspace invalidates pending tree responses', async () => {
    const pending = deferred();
    const { context, state } = fixture({ intercept: (url) => url.endsWith('/tree') ? pending.promise : undefined });
    const loading = context.loadMaterialTree(100);
    context.resetMaterialWorkspace();
    pending.resolve({ tree: freshTree(), stats: {} });
    await loading;
    assert.equal(state.materialWorkspace.root, null);
    assert.equal(state.materialWorkspace.treeLoading, false);
});

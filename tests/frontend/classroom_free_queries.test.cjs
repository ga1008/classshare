/* Execute production controllers with delayed synthetic APIs; no app or database. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function functions(file, names, indent = '') {
    const source = fs.readFileSync(file, 'utf8');
    return names.map(name => {
        const match = source.match(new RegExp(`^${indent}(?:async )?function ${name}\\([\\s\\S]*?^${indent}}`, 'm'));
        assert.ok(match, `Production function ${name} exists`);
        return match[0];
    }).join('\n');
}
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
    return { promise, resolve, reject };
};
const room = id => ({ place_id: id, display_name: `Room ${id}` });
const success = (items = [room('A')], rest = {}) => ({ status: 'success', items, total_count: items.length, ...rest });

function manageFixture(apiFetch) {
    const refs = Object.fromEntries(['freeSubmit', 'freeSemester', 'freeCampus', 'freeBuilding', 'freeType', 'freeName',
        'freeResultList', 'freeResultEmpty', 'freeResultSummary', 'freeResultTerm', 'freePagination'].map(key => [key, { value: '', textContent: '', innerHTML: '', hidden: false, disabled: false }]));
    refs.freeSemester.value = '5'; refs.freeCampus.value = '1';
    const state = { selectedWeek: 6, selectedWeekday: 2, selectedSections: new Set([2, 3]),
        freeOptionsRequest: 0, freeOptionsLoading: false, freeQueryRequest: 0, freeQueryLoading: false, freeQuerySnapshot: null };
    const notices = [], optionUpdates = [];
    const context = vm.createContext({ state, refs, URLSearchParams, console, apiFetch,
        showMessage: message => notices.push(message), renderSections() {},
        renderFreeRecommendations() {}, renderFreeCard: item => `<article>${item.place_id}</article>`,
        updateSelectOptions: (_, items) => optionUpdates.push(items),
        tableMarkup: (_, props) => JSON.stringify(props),
    });
    vm.runInContext(functions('static/js/manage_classrooms.js', ['numberValue', 'freeOptionParams', 'loadFreeOptions',
        'updateFreeQueryBusy', 'clearFreeQuery', 'selectedSections', 'freeQueryPayload', 'describeFreeQuery', 'queryFreeRooms']), context);
    return { context, state, refs, notices, optionUpdates };
}

test('a query response for changed criteria never reappears with the new criteria label', async () => {
    const pending = deferred();
    const { context, state, refs } = manageFixture(() => pending.promise);
    const request = context.queryFreeRooms();
    state.selectedWeek = 7; context.clearFreeQuery();
    pending.resolve(success()); await request;
    assert.equal(refs.freeResultList.innerHTML, '');
    assert.equal(state.freeQuerySnapshot, null);
    assert.equal(refs.freeSubmit.disabled, false);
    assert.match(refs.freeResultEmpty.textContent, /条件已改变/);
});

test('an obsolete failure cannot reset a newer query busy state or show an error', async () => {
    const first = deferred(), second = deferred(); let count = 0;
    const { context, refs, notices } = manageFixture(() => (++count === 1 ? first : second).promise);
    const a = context.queryFreeRooms(), b = context.queryFreeRooms();
    first.reject(new Error('obsolete')); await a;
    assert.equal(refs.freeSubmit.disabled, true);
    assert.deepEqual(notices, []);
    second.resolve(success([room('new')])); await b;
    assert.match(refs.freeResultList.innerHTML, /new/);
    assert.equal(refs.freeSubmit.disabled, false);
});

test('late options cannot replace the current campus options or unlock its query', async () => {
    const first = deferred(), second = deferred(); let count = 0;
    const { context, refs, optionUpdates } = manageFixture(() => (++count === 1 ? first : second).promise);
    const a = context.loadFreeOptions(); refs.freeCampus.value = '2'; const b = context.loadFreeOptions();
    first.resolve({ options: { buildings: ['old'] } }); await a;
    assert.equal(refs.freeSubmit.disabled, true); assert.deepEqual(optionUpdates, []);
    second.resolve({ options: { buildings: ['new'], room_types: [] } }); await b;
    assert.deepEqual(optionUpdates, [['new'], []]); assert.equal(refs.freeSubmit.disabled, false);
});

test('server pagination retains the submitted slot and renders the shared pager', async () => {
    const calls = [];
    const { context, state, refs } = manageFixture(async (_, options) => {
        calls.push(options.body); return success([room(`page-${options.body.page}`)], { total_count: 130, total_page: 2, page: options.body.page });
    });
    await context.queryFreeRooms();
    const snapshot = state.freeQuerySnapshot;
    assert.equal(refs.freePagination.hidden, false);
    assert.equal(JSON.parse(refs.freePagination.innerHTML).totalPages, 2);
    state.selectedWeek = 9;
    await context.queryFreeRooms(null, { page: 2, snapshot });
    assert.equal(calls[1].page, 2); assert.equal(calls[1].weeks[0], 6);
    assert.match(refs.freeResultSummary.textContent, /第 6 周/);
});

test('current failure removes old free cards and remains distinct from a successful empty query', async () => {
    let fail = false;
    const { context, refs } = manageFixture(async () => { if (fail) throw new Error('教务暂不可用 <retry>'); return success([]); });
    await context.queryFreeRooms(); assert.match(refs.freeResultEmpty.innerHTML, /没有可用教室/);
    refs.freeResultList.innerHTML = 'old free room'; fail = true; await context.queryFreeRooms();
    assert.equal(refs.freeResultList.innerHTML, ''); assert.equal(refs.freeResultSummary.textContent, '查询未完成');
    assert.equal(refs.freeResultEmpty.textContent, '教务暂不可用 <retry>');
});

function editorFixture(api) {
    const state = { payload: { editable: true }, form: { key: 'lesson-A', week: 6, weekday: 2, start: 2, span: 2, room_id: '', room: '' },
        availability: {}, freeRooms: { slotKey: '', items: [], loading: false, page: 0, hasMore: false } };
    const list = { innerHTML: '' }, button = { dataset: { cseLocked: 'false' }, disabled: false, textContent: '' };
    const refs = { drawer: { querySelector: selector => selector === '[data-cse-free-rooms-list]' ? list : button } };
    const notices = [];
    const context = vm.createContext({ state, refs, URLSearchParams, API: '/editor', console, api,
        AVAIL_LABELS: { block: '学生有课', teacher: '本人有课' },
        term: () => ({ year: '2026-2027', term: '1' }), resolveSelection: key => ({ lesson: { classroom: `Original-${key}` } }),
        effectiveSlot: (week, weekday) => ({ week, weekday }), escapeHtml: value => String(value).replaceAll('<', '&lt;'),
        toast: message => notices.push(message), renderWeekRail() {}, renderStage() {}, renderLegend() {}, renderDrawerVerdict() {},
    });
    vm.runInContext('let availabilityRequestId = 0; let freeRoomsRequestId = 0;\n' + functions('static/js/course_schedule_editor.js',
        ['currentFormSections', 'freeRoomSlotKey', 'renderFreeRooms', 'searchFreeRooms', 'loadAvailability', 'cellState', 'cellReason', 'slotVerdict'], '    '), context);
    return { context, state, list, button, notices };
}

test('changing the lesson or selected classroom prevents a late editor result from becoming selectable', async () => {
    const pending = deferred();
    const { context, state, list } = editorFixture(() => pending.promise);
    const loading = context.searchFreeRooms();
    state.form = { ...state.form, key: 'lesson-B', room_id: 'B', room: 'New room' }; context.renderFreeRooms();
    pending.resolve({ result: success([room('stale')]) }); await loading;
    assert.equal(state.freeRooms.items.length, 0); assert.doesNotMatch(list.innerHTML, /data-cse-room=/);
});

test('editor fetches later pages and exposes the 41st classroom without losing earlier choices', async () => {
    const urls = [];
    const { context, state, list } = editorFixture(async url => {
        urls.push(url); const page = Number(new URL(url, 'http://fixture').searchParams.get('page'));
        return { result: success(Array.from({ length: page === 1 ? 40 : 1 }, (_, i) => room(`${page}-${i}`)), { page, total_count: 41, total_page: 2, has_more: page === 1 }) };
    });
    await context.searchFreeRooms(); assert.match(list.innerHTML, /data-cse-free-more/);
    await context.searchFreeRooms({ append: true });
    assert.equal(state.freeRooms.items.length, 41); assert.match(list.innerHTML, /data-cse-room="2-0"/);
    assert.doesNotMatch(list.innerHTML, /data-cse-free-more/);
    assert.match(urls[1], /page=2/); assert.match(urls[1], /page_size=40/);
});

test('later-page failure retains valid prior choices and retry uses the same next page', async () => {
    let count = 0; const pages = [];
    const { context, state, list } = editorFixture(async url => {
        const page = Number(new URL(url, 'http://fixture').searchParams.get('page')); pages.push(page);
        if (++count === 2) throw new Error('second page unavailable');
        return { result: success([room(`page-${page}`)], { total_count: 2, total_page: 2, has_more: page === 1 }) };
    });
    await context.searchFreeRooms(); await context.searchFreeRooms({ append: true });
    assert.equal(state.freeRooms.page, 1); assert.match(list.innerHTML, /page-1/); assert.match(list.innerHTML, /second page unavailable/);
    await context.searchFreeRooms({ append: true }); assert.deepEqual(pages, [1, 2, 2]);
    assert.equal(state.freeRooms.items.length, 2);
});

test('successful candidates keep an unverified original classroom explicitly unknown', async () => {
    const { context, state, list } = editorFixture(async () => ({ result: success([room('available')], {
        room_status: 'unknown', room_status_message: '当前教室尚未同步 <verify>',
    }) }));
    await context.searchFreeRooms();
    assert.equal(state.freeRooms.roomStatus, 'unknown');
    assert.match(list.innerHTML, /当前教室尚未同步 &lt;verify>/);
    assert.match(list.innerHTML, /data-cse-room="available"/);
    assert.doesNotMatch(list.innerHTML, /当前教室该时段已被占用|当前教室该时段空闲/);
});

test('old availability errors cannot erase the new lesson availability', async () => {
    const first = deferred(), second = deferred(); let count = 0;
    const { context, state, notices } = editorFixture(() => (++count === 1 ? first : second).promise);
    const a = context.loadAvailability('A'), b = context.loadAvailability('B');
    second.resolve({ availability: { current: true } }); await b;
    first.reject(new Error('obsolete')); await a;
    assert.equal(state.availability.key, 'B'); assert.equal(state.availability.data.current, true); assert.deepEqual(notices, []);
});

test('a grouped room conflict only blocks slots containing the complete checked group', () => {
    const { context } = editorFixture();
    const data = { room_busy_blocks: [{ week: 6, weekday: 2, sections: [2, 3], detail: '整组不可用' }] };
    assert.equal(context.slotVerdict(data, 6, 2, [2, 3]).level, 'room');
    assert.equal(context.slotVerdict(data, 6, 2, [2, 3, 4]).level, 'room');
    assert.equal(context.slotVerdict(data, 6, 2, [2]).level, 'unknown');
    assert.equal(context.slotVerdict(data, 6, 2, [3, 4]).level, 'unknown');
    assert.equal(context.slotVerdict(data, 7, 2, [2, 3]).level, 'unknown');
});

test('known free sections and student conflicts retain their own certainty and priority', () => {
    const { context } = editorFixture();
    const data = { room_busy_blocks: [{ week: 6, weekday: 2, sections: [2, 3] }], room_checked: { 6: { 2: { 2: 'free' } } } };
    assert.equal(context.slotVerdict(data, 6, 2, [2]).level, 'ok');
    data.students = { 6: { 2: { 2: '其他课程' } } };
    assert.equal(context.slotVerdict(data, 6, 2, [2, 3]).level, 'block');
});

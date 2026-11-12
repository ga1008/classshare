/** Teacher review of server-owned course/lesson candidates; never builds a binding client-side. */
import { openDialog } from './lq/dialogs.js';
import { createDomainButton, adoptDomainControl } from './lq/domain-controls.js';
import { createComponent } from './lq/components.js';
import { getLayerSystem } from './lq/layer.js';

const el = (tag, text) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); return node; };
const shown = value => value == null || value === '' ? '未提供' : String(value);
const scopeName = scope => scope === 'course' ? '课程' : '课次';
const stateName = state => ({ matched: '已对齐', review: '需要确认', stale: '需要重新对齐', unmatched: '未对齐' }[state] || '未对齐');

function facts(target = {}, scope, inButton = false) {
    target ||= {};
    const list = el(inButton ? 'span' : 'dl'); list.className = 'la-alignment-facts';
    const entries = scope === 'course' ? [
        ['学年 / 学期', [target.academic_year, target.academic_term == null ? null : `第 ${target.academic_term} 学期`].filter(Boolean).join(' · ')],
        ['课程名称', target.course_name], ['课程代码', target.course_code],
        ['学生人数', target.student_count == null ? null : `${target.student_count} 人`],
        ['课次总数', target.session_count == null ? null : `${target.session_count} 次`],
        ['总学时 / 学分', `${shown(target.total_hours)} 学时 · ${shown(target.credits)} 学分`],
    ] : [
        ['上课日期', target.session_date], ['教学周', target.week == null ? null : `第 ${target.week} 周`],
        ['星期', target.day_of_week == null ? null : ({ 0: '星期日', 1: '星期一', 2: '星期二', 3: '星期三', 4: '星期四', 5: '星期五', 6: '星期六', 7: '星期日' }[target.day_of_week] || target.day_of_week)],
        ['节次', target.section_text || (Array.isArray(target.sections) ? target.sections.join('、') : target.sections)],
        ['课次序号', target.order_index == null ? null : `第 ${target.order_index} 次课`],
    ];
    for (const [label, value] of entries) { const row = el(inButton ? 'span' : 'div'); row.append(el(inButton ? 'span' : 'dt', label), el(inButton ? 'span' : 'dd', shown(value))); list.append(row); }
    return list;
}

function evidence(candidate) {
    const block = el('div'); block.className = 'la-alignment-evidence';
    const roster = candidate.evidence?.roster;
    if (roster) {
        block.append(el('p', `名单：本地 ${shown(roster.local_count)} 人 · 学校 ${shown(roster.remote_count)} 人 · 相同 ${shown(roster.matched_count)} 人`));
        block.append(el('p', `差异 ${shown(roster.difference_count)} 人（仅本地 ${shown(roster.local_only_count)} 人，仅学校 ${shown(roster.remote_only_count)} 人）`));
    }
    for (const reason of candidate.reasons || []) block.append(el('p', typeof reason === 'string' ? reason : reason.message || reason.reason || '请核对候选信息'));
    return block;
}

export function openAttendanceAlignment({ value, trigger, parentLayer, confirm, realign, onClose }) {
    let snapshot = value, scope = value.alignment?.course?.state === 'matched' ? 'session' : 'course';
    let busy = false, closed = false, handle, intent = null;
    const selected = { course: null, session: null }, candidateButtons = [];
    const body = el('div'); body.className = 'la-alignment la-workspace';
    const intro = el('p', '先核对课程，再核对本次课。选择右侧学校卡片后确认；确认前不会开启或修改学校点名。');
    const steps = el('div'); steps.className = 'la-actions'; steps.setAttribute('aria-label', '核对层级');
    const course = createDomainButton({ label: '1 · 课程', variant: 'glass', size: 'sm' });
    const session = createDomainButton({ label: '2 · 课次', variant: 'glass', size: 'sm' }); steps.append(course, session);
    const status = el('p'); status.setAttribute('role', 'status');
    const courseContext = el('p');
    const comparison = el('div'); comparison.className = 'la-alignment-comparison';
    const policy = el('small', '名单以智慧课堂为准；少量差异由系统核验，本地账号和已记录的签到不会删除。');
    body.append(intro, steps, courseContext, status, comparison, policy);
    const footer = el('div'); footer.className = 'la-actions';
    const retry = createDomainButton({ label: '重新对齐', variant: 'glass', size: 'sm' });
    const cancel = createDomainButton({ label: '暂不确认', variant: 'ghost', size: 'sm' });
    const save = createDomainButton({ label: '确认课程', variant: 'prominent', size: 'sm' }); footer.append(retry, cancel, save);
    const current = () => snapshot.alignment?.[scope] || {};
    function invalidReason() {
        if (current().state === 'stale') return '信息已变化，请重新对齐。旧卡片仅供对照，不能直接确认。';
        if (current().expires_at_ms != null && Number(current().expires_at_ms) <= Date.now()) return '候选已过期，请重新对齐。旧卡片仅供对照，不能直接确认。';
        return '';
    }
    function disabled(node, value) { node.disabled = value; node.setAttribute('aria-disabled', String(value)); node.dataset.lqDisabled = String(value); }
    function gate() {
        const candidate = (current().candidates || []).find(item => item.id === selected[scope]);
        const invalid = Boolean(invalidReason());
        if (invalid && !busy) { status.setAttribute('role', 'status'); status.textContent = invalidReason(); }
        disabled(save, busy || invalid || !candidate?.eligible); disabled(retry, busy); disabled(cancel, busy); disabled(course, busy);
        disabled(session, busy || snapshot.alignment?.course?.state !== 'matched');
        for (const { button, candidate } of candidateButtons) disabled(button, busy || invalid || !candidate.eligible);
        body.setAttribute('aria-busy', String(busy)); save.setAttribute('aria-busy', String(busy));
    }
    function updateSelection() {
        for (const { button, candidate } of candidateButtons) button.setAttribute('aria-pressed', String(candidate.id === selected[scope]));
        gate();
    }
    function render() {
        const layer = current(); candidateButtons.length = 0; comparison.replaceChildren();
        course.setAttribute('aria-pressed', String(scope === 'course')); session.setAttribute('aria-pressed', String(scope === 'session'));
        const alignedCourse = snapshot.alignment?.course, courseTarget = (alignedCourse?.candidates || []).find(item => item.id === alignedCourse.selected_id)?.target;
        courseContext.hidden = scope !== 'session' || !courseTarget;
        courseContext.textContent = courseTarget ? `已对齐课程：${shown(courseTarget.course_name)} · ${shown(courseTarget.course_code)} · ${shown(courseTarget.academic_year)} 第 ${shown(courseTarget.academic_term)} 学期` : '';
        save.textContent = `确认${scopeName(scope)}`;
        status.setAttribute('role', 'status'); status.textContent = invalidReason() || layer.message || `${scopeName(scope)}${stateName(layer.state)}，请核对左右信息。`;
        const local = el('section'); local.className = 'la-alignment-column';
        local.append(el('h3', `本地${scopeName(scope)}`));
        const localCard = el('article'); localCard.className = 'lq-surface la-alignment-card'; localCard.dataset.lqComponent = 'surface'; localCard.dataset.lqPadding = 'md';
        localCard.append(facts(layer.local, scope)); local.append(localCard);
        const remote = el('section'); remote.className = 'la-alignment-column'; remote.append(el('h3', `智慧课堂${scopeName(scope)}`));
        const candidates = layer.candidates || [];
        if (!candidates.some(item => item.id === selected[scope])) selected[scope] = candidates.some(item => item.id === layer.selected_id) ? layer.selected_id : null;
        if (!candidates.length) remote.append(el('p', layer.state === 'stale' ? '候选已过期，请重新对齐后再确认。' : '暂时没有可供确认的学校记录。点击重新对齐，系统会读取最新课表并说明原因。'));
        for (const candidate of candidates) {
            const card = el('article'); card.className = 'la-alignment-candidate';
            const button = createDomainButton({ label: candidate.label || `选择学校${scopeName(scope)}`, variant: 'glass', size: 'sm' });
            adoptDomainControl(button, { kind: 'choice' }); button.dataset.lqShape = 'surface'; button.classList.add('la-alignment-card');
            button.replaceChildren(el('strong', candidate.label || `学校${scopeName(scope)}`), facts(candidate.target, scope, true));
            button.setAttribute('aria-label', `选择${scopeName(scope)}：${candidate.label || candidate.id}`);
            button.addEventListener('click', () => { if (busy || !candidate.eligible) return; if (selected[scope] !== candidate.id) intent = null; selected[scope] = candidate.id; updateSelection(); });
            if (candidate.id === layer.selected_id) button.append(createComponent('chip', { label: '当前已对齐', kind: 'status', tone: 'success' }));
            if (!candidate.eligible) button.append(createComponent('chip', { label: '暂不能确认', kind: 'status', tone: 'warning' }));
            card.append(button, evidence(candidate)); remote.append(card); candidateButtons.push({ button, candidate });
        }
        comparison.append(local, remote); updateSelection();
    }
    async function run(action) {
        if (closed || busy) return;
        busy = true; gate(); status.setAttribute('role', 'status'); status.textContent = '正在读取学校记录并核验选择，请稍候；本地点名不受影响。';
        try {
            const result = await action(); if (closed) return;
            snapshot = result; intent = null;
            if (snapshot.binding?.state === 'matched') { busy = false; await getLayerSystem(document).close(handle, 'confirmed'); return; }
            scope = snapshot.alignment?.course?.state === 'matched' ? 'session' : 'course'; render();
        } catch (error) { if (!closed) { status.setAttribute('role', 'alert'); status.textContent = error.message || '确认暂未完成，选择已保留。请重试或重新对齐。'; } }
        finally { busy = false; if (!closed) gate(); }
    }
    course.addEventListener('click', () => { if (!busy) { scope = 'course'; render(); } });
    session.addEventListener('click', () => { if (!busy && snapshot.alignment?.course?.state === 'matched') { scope = 'session'; render(); } });
    retry.addEventListener('click', () => run(realign));
    cancel.addEventListener('click', () => getLayerSystem(document).close(handle, 'cancel'));
    save.addEventListener('click', () => {
        const invalid = invalidReason(); if (invalid) { status.setAttribute('role', 'alert'); status.textContent = invalid; gate(); return; }
        const candidate = (current().candidates || []).find(item => item.id === selected[scope]); if (!candidate?.eligible || busy) return;
        const data = { scope, candidate_id: candidate.id, expected_course_revision: snapshot.alignment?.course?.revision ?? 0, expected_binding_revision: snapshot.binding?.revision ?? 0 };
        const key = JSON.stringify(data); if (intent?.key !== key) intent = { key, id: crypto.randomUUID() };
        run(() => confirm({ ...data, idempotency_key: intent.id }));
    });
    const release = () => { if (closed) return; closed = true; onClose?.(); };
    render();
    handle = openDialog({ title: '存疑课次核对', body, footer, size: 'xl' }, { trigger, parentLayer, beforeClose: () => !busy, onClose: release, onDestroy: release });
    handle.root.classList.add('la-dialog');
    return handle;
}

import { apiFetch } from './api.js';
import { showToast } from './ui.js';
import { createDialog, openDialog, confirm } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { escapeHtml as h } from './lq/html.js';
import { iconMarkup } from './lq/icons.js';
import { button, linkButton, check, field, input, textarea, select, disclosure, issues, fileLinks, packageOverview,
    mappingForm, topicEditor, updateManifest, publishForm, bindingFields, topicBody, topicName, activityStates, readiness } from './project_topic_forms.js';

const newId = () => globalThis.crypto?.randomUUID?.() ?? `pt-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const versionLabel = p => `${p.title} · ${p.version}`;
const pkgFiles = (base, id) => `${base}/packages/${encodeURIComponent(id)}/files`;
const topicFiles = (base, id, key) => `${base}/activities/${encodeURIComponent(id)}/topics/${encodeURIComponent(key)}/files`;

function importForm(open) {
    const picker = (name, label, icon, attrs) => `<label class="pt-picker lq-btn lq-btn--glass" data-lq-component="button">
        ${iconMarkup(icon)}<span>${label}</span><input name="${name}" type="file" aria-label="${label}" ${attrs}></label>`;
    return `<details class="pt-disclosure pt-import"${open ? ' open' : ''}><summary data-pt-import-toggle class="lq-disclosure-trigger" data-lq-component="disclosure">
        ${iconMarkup('upload')}<strong>导入题包</strong>${iconMarkup('chevron-down')}</summary>
        <form class="pt-stack" data-pt-form="import">
            <div class="pt-picker-row">${picker('zip', '选择 ZIP', 'file-archive', 'accept=".zip"')}
                ${picker('folder', '选择文件夹', 'folder-open', 'webkitdirectory multiple')}</div>
            <div class="pt-stack" data-pt-upload-options hidden>
                <output class="pt-file-selection" aria-live="polite"></output>
                ${field('题包名称（选填）', input('title', '', 'maxlength="160"'))}
                ${button('导入', 'import', { submit: true, variant: 'prominent', icon: 'upload' })}
            </div>
        </form></details>`;
}

function entryList(items, action, icon, meta) {
    return `<ul class="pt-entry-list">${items.map(item => `<li><button type="button" data-lq-component="button"
        class="lq-btn lq-btn--ghost pt-open-row" data-pt-action="${action}" data-id="${h(item.id)}">
        ${iconMarkup(icon)}<span class="pt-entry-title">${h(item.title)}</span>
        <small class="pt-meta">${h(meta(item))}</small>${iconMarkup('chevron-right')}</button></li>`).join('')}</ul>`;
}

function metrics(items) {
    return `<dl class="pt-metrics">${items.map(([key, icon, label, value]) => `<div data-pt-metric="${key}">
        <dt>${iconMarkup(icon)}${h(label)}</dt><dd>${h(value)}</dd></div>`).join('')}</dl>`;
}

function downloadManifest(manifest) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'project-topics.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function openProjectTopics({ classId, getSnapshot, refreshGroups, findJoinButton, trigger, schemeId = null }) {
    const teacher = getSnapshot().role === 'teacher';
    const base = `/api/classrooms/${encodeURIComponent(classId)}/project-topics`;
    const body = document.createElement('div');
    body.className = 'pt-workspace';
    const root = createDialog({ title: teacher ? '派发项目题目' : '本组题目', body, size: 'lg' });
    root.classList.add('pt-dialog');
    const screenTitle = root.querySelector('.lq-dialog__head h2');
    screenTitle.tabIndex = -1;
    screenTitle.insertAdjacentHTML('beforebegin', button('返回项目列表', 'home', { icon: 'arrow-left', iconOnly: true, variant: 'ghost' }));
    const backButton = screenTitle.previousElementSibling;
    backButton.hidden = true;
    const controller = new AbortController();
    let busy = false, writing = false, dirty = false, focusTitle = false, canReturn = true, dropdowns = [], returnTo, navigationOrigin;
    let packages = [], pkg, activity, manifest, editIndex, preview, publishIntent, schemeGroups, autoPlan, draftRevision;
    let refreshAfterPublish;
    const cleanup = () => {
        controller.abort();
        releaseDropdowns(body);
    };
    const discard = () => !dirty || confirm({ title: '放弃本次编辑？', message: '尚未保存的输入会丢失。', confirmLabel: '放弃编辑', cancelLabel: '继续编辑' }, { parentLayer: handle });
    const handle = openDialog(root, { trigger, beforeClose: () => !writing && discard(), onClose: cleanup, onDestroy: cleanup });
    const q = s => body.querySelector(s);
    const request = (path, options) => apiFetch(`${base}${path}`, { silent: true, signal: controller.signal, ...options });
    const post = (path, data) => request(path, { method: 'POST', body: data });

    function releaseDropdowns(section) {
        dropdowns = dropdowns.filter(binding => {
            if (!section.contains(binding.select)) return true;
            binding.destroy();
            return false;
        });
    }
    function replace(section, html) {
        releaseDropdowns(section);
        section.innerHTML = html;
        dropdowns.push(...enhanceDropdowns(section));
    }
    function draw(html, back = true, title = teacher ? '派发项目题目' : '本组题目', parent = 'home') {
        if (controller.signal.aborted) return;
        focusTitle = Boolean(navigationOrigin && body.querySelector('form, section, [data-pt-action="retry"]'));
        screenTitle.textContent = title;
        returnTo = parent;
        backButton.title = parent === 'package' ? '返回题包' : parent === 'activity' ? '返回活动' : '返回项目列表';
        backButton.setAttribute('aria-label', backButton.title);
        backButton.hidden = !back;
        replace(body, `<div data-pt-error role="alert" hidden></div>${html}`);
        dirty = false;
        const scroller = body.closest('.lq-dialog__body');
        if (scroller) scroller.scrollTop = 0;
    }
    async function task(fn, mutation = false) {
        if (busy || controller.signal.aborted) return;
        busy = true;
        writing = mutation;
        navigationOrigin = root.contains(document.activeElement) ? document.activeElement : null;
        body.inert = true;
        body.setAttribute('aria-busy', 'true');
        if (q('[data-pt-error]')) q('[data-pt-error]').hidden = true;
        try {
            await fn();
        } catch (error) {
            if (controller.signal.aborted) return;
            const message = error.message || '操作失败，请重试。';
            const area = q('[data-pt-error]');
            if (area) {
                area.hidden = false;
                area.textContent = message;
                const loading = q('[data-pt-loading]');
                if (loading) loading.outerHTML = button('重新加载', 'retry', {icon:'refresh-cw'});
                area.scrollIntoView({ block: 'nearest' });
            } else showToast(message, 'error');
        } finally {
            busy = false;
            writing = false;
            body.inert = false;
            body.removeAttribute('aria-busy');
            if (!controller.signal.aborted) {
                if (focusTitle) screenTitle.focus({preventScroll:true});
                else if (navigationOrigin?.isConnected && document.activeElement === document.body) navigationOrigin.focus({preventScroll:true});
            }
            focusTitle = false;
            navigationOrigin = null;
        }
    }

    async function home(openOnlyActivity = false) {
        const [catalog, list] = await Promise.all([teacher ? request('/packages') : { packages: [] }, request('/activities')]);
        packages = catalog.packages;
        const activities = list.activities.filter(a => !schemeId || String(a.scheme_id) === String(schemeId));
        canReturn = teacher || activities.length !== 1;
        if (openOnlyActivity && !canReturn) return showActivity(activities[0].id);
        const entries = activities.length ? `<section class="pt-stack"><h3 class="pt-section-title">课堂项目</h3>
            ${entryList(activities, 'activity', 'book-open', a => activityStates[a.state])}</section>` : '';
        if (!teacher) {
            draw(entries || `<p class="pt-empty">${iconMarkup('book-open')}还没有已发布的项目</p>`, false);
            return;
        }
        const versions = new Map();
        const label = p => JSON.stringify([p.title, p.version]);
        for (const p of packages) versions.set(label(p), (versions.get(label(p)) || 0) + 1);
        draw(`${entries}<section class="pt-stack"><h3 class="pt-section-title">我的题包</h3>
            ${importForm(!packages.length)}${packages.length ? entryList(packages, 'package', 'package-open', p =>
                `${p.version}${versions.get(label(p)) > 1 ? ' · #' + p.id : ''}${p.mapping_confirmed === false ? ' · 待核对' : ''}`) : ''}</section>`, false);
    }

    function displayPackage(value) {
        pkg = value;
        const confirmed = pkg.mapping_confirmed !== false;
        draw(`<section class="pt-stack"><div class="pt-title-group"><small class="pt-meta">${h(pkg.version)}</small><h3 class="pt-title">${h(pkg.title)}</h3></div>
            <div class="pt-actions">${button('准备派题', 'prepare', { variant: confirmed ? 'prominent' : 'glass', disabled: !confirmed })}
            ${button(confirmed ? '编辑题目' : '核对题目', 'edit', {variant: confirmed ? 'glass' : 'prominent'})}
            ${button('导出题包清单', 'manifest', {icon: 'download', iconOnly: true, variant: 'ghost'})}</div>
            ${packageOverview(pkg, pkgFiles(base, pkg.id))}</section>`, true, '题包');
    }
    async function importPackage(form) {
        const zip = form.elements.zip.files[0];
        const folder = Array.from(form.elements.folder.files);
        if (Boolean(zip) === Boolean(folder.length)) throw new Error('请选择一个 ZIP 或一个文件夹。');
        const data = new FormData();
        const files = zip ? [zip] : folder;
        for (const file of files) data.append('files', file, file.name);
        data.append('paths', JSON.stringify(files.map(f => f.webkitRelativePath || f.name)));
        if (form.elements.title.value.trim()) data.append('title', form.elements.title.value.trim());
        displayPackage((await post('/packages/import', data)).package);
        showToast('已导入教师私有题包', 'success');
    }
    function startMapping() {
        manifest = structuredClone(pkg.manifest);
        editIndex = 0;
        draw(mappingForm(pkg, manifest), true, '核对题目', 'package');
        renderEditor();
    }
    function renderEditor() {
        replace(q('[data-pt-edit-slot]'), topicEditor(pkg, manifest, editIndex));
    }

    async function prepare() {
        const [{ draft }] = await Promise.all([request(`/packages/${pkg.id}/dispatch-draft`), refreshAfterPublish]);
        preview = null;
        publishIntent = null;
        autoPlan = null;
        draftRevision = draft?.revision || 0;
        draw(`<div class="pt-context">${iconMarkup('package-open')}<span>${h(versionLabel(pkg))}</span></div>${publishForm(pkg, getSnapshot())}`, true, '准备派题', 'package');
        const form = q('form');
        const data = draft?.payload;
        if (!data) {
            updateBindings(form);
            return;
        }
        const fields = form.elements;
        fields.title.value = data.title || '';
        fields.scheme.value = data.new_scheme ? 'new' : String(data.scheme_id || fields.scheme.value);
        if (!fields.scheme.value && data.scheme_id) fields.scheme.add(new Option('草稿中的分组方案已不可用', String(data.scheme_id), true, true));
        fields.mode.value = data.mode || 'random';
        fields.allow_brief.checked = data.allow_brief === true;
        if (fields.assignment_id) fields.assignment_id.value = data.assignment_id || '';
        if (data.new_scheme) {
            fields.scheme_name.value = data.new_scheme.name || '';
            for (const name of ['min_members', 'max_members', 'group_count', 'allocation_mode']) fields[name].value = data.new_scheme[name] ?? '';
            if (!data.new_scheme.group_count && data.bindings?.length) {
                const last = Math.min(60, Math.max(0, ...data.bindings.map(b => Number(b.group_index) || 0)));
                autoPlan = Array.from({ length: last }, (_, i) => ({ group_index: i + 1, name: `第 ${i + 1} 组` }));
            }
        }
        const selected = new Set(data.topic_keys || []);
        form.querySelectorAll('[name="topic_keys"]').forEach((box, i) => {
            const status = pkg.topics[i].readiness;
            box.disabled = status === 'missing' || (status === 'brief' && !fields.allow_brief.checked);
            box.checked = !box.disabled && selected.has(box.value);
        });
        updateBindings(form);
        if (!data.new_scheme && Array.isArray(data.group_ids)) {
            const included = new Set(data.group_ids.map(String));
            form.querySelectorAll('[name="group_ids"]').forEach(box => { box.checked = included.has(box.value); });
            updateBindings(form);
        }
        for (const binding of data.bindings || []) {
            const control = fields.namedItem(`binding-${binding.group_id ?? binding.group_index}`);
            if (control) control.value = binding.topic_key || '';
        }
        q('[data-pt-draft-status]').textContent = '已恢复草稿';
    }
    async function saveDraft() {
        const status = q('[data-pt-draft-status]');
        try {
            const { draft } = await request(`/packages/${pkg.id}/dispatch-draft`, { method: 'PUT',
                body: { revision: draftRevision, payload: publishData(q('form')) } });
            draftRevision = draft.revision;
            dirty = false;
            status.textContent = '草稿已保存';
        } catch (error) {
            if (error.status === 409) status.innerHTML = button('读取最新派题草稿', 'reload-draft');
            throw error;
        }
    }
    function updateBindings(form) {
        const fields = form.elements;
        const isNew = fields.scheme.value === 'new';
        q('[data-pt-new]').hidden = !isNew;
        q('[data-pt-new]').querySelectorAll('input,select').forEach(el => { el.disabled = !isNew; });
        const scheme = getSnapshot().schemes.find(s => String(s.id) === fields.scheme.value);
        const participants = q('[data-pt-participants]');
        const schemeChanged = participants.dataset.scheme !== fields.scheme.value;
        if (schemeChanged) {
            participants.dataset.scheme = fields.scheme.value;
            replace(participants, isNew ? '' : disclosure('参与小组', (scheme?.groups || []).map(g =>
                check('group_ids', g.id, `${g.name} · ${g.member_count} 人`, { checked: true })).join('')));
        }
        const count = Number(fields.group_count.value);
        if (isNew && count > 0) schemeGroups = Array.from({ length: Math.min(60, count) }, (_, i) => ({ group_index: i + 1, name: `第 ${i + 1} 组` }));
        else if (isNew) schemeGroups = autoPlan || [];
        else {
            const included = new Set(new FormData(form).getAll('group_ids'));
            schemeGroups = (scheme?.groups || []).filter(g => included.has(String(g.id)));
        }
        const selected = new Set(new FormData(form).getAll('topic_keys'));
        q('[data-pt-pool-count]').textContent = `已选 ${selected.size} / ${pkg.topics.length}`;
        const options = pkg.topics.filter(t => selected.has(t.key)).map(t => [t.key, topicName(t)]);
        const previous = Object.fromEntries(schemeGroups.map(g => {
            const ref = g.id ?? g.group_index;
            return [ref, schemeChanged ? '' : fields.namedItem(`binding-${ref}`)?.value];
        }));
        const holder = q('[data-pt-bindings]');
        replace(holder, fields.mode.value === 'manual'
            ? (isNew && !count && !autoPlan ? '<p>先预检组数，再指定题目。</p>' : bindingFields(schemeGroups, options, previous)) : '');
    }
    function publishData(form) {
        const values = new FormData(form);
        const mode = values.get('mode');
        const isNew = values.get('scheme') === 'new';
        const data = { title: values.get('title').trim(), package_version_id: pkg.id, mode,
            allow_brief: values.has('allow_brief'), topic_keys: values.getAll('topic_keys') };
        if (values.get('assignment_id')) data.assignment_id = values.get('assignment_id');
        if (isNew) data.new_scheme = { name: values.get('scheme_name').trim(), min_members: Number(values.get('min_members')),
            max_members: Number(values.get('max_members')), group_count: values.get('group_count') ? Number(values.get('group_count')) : null,
            allocation_mode: values.get('allocation_mode') };
        else {
            data.scheme_id = Number(values.get('scheme'));
            data.group_ids = schemeGroups.map(g => g.id);
        }
        if (mode === 'manual') data.bindings = schemeGroups.map(g => ({
            ...(isNew ? { group_index: g.group_index } : { group_id: g.id }), topic_key: values.get(`binding-${g.id ?? g.group_index}`) }));
        return data;
    }
    async function preflight(form) {
        const payload = publishData(form);
        const result = (await post('/preview', payload)).preview;
        if (payload.mode === 'manual' && payload.new_scheme && !payload.new_scheme.group_count) {
            autoPlan = result.groups;
            updateBindings(form);
        }
        preview = result.ready ? { payload, token: result.preview_token } : null;
        const publicCopies = (result.public_copies || []).map(row => `${row.path} → ${row.material_path}`);
        const distribution = new Map();
        for (const group of result.groups || []) distribution.set(group.member_count, (distribution.get(group.member_count) || 0) + 1);
        const sizes = Array.from(distribution, ([n, count]) => `${count} 组各 ${n} 人`).join('，');
        q('[data-pt-preview]').innerHTML = `<section class="pt-stack pt-result">
            <h4 class="pt-section-title">${iconMarkup(result.ready ? 'circle-check' : 'circle-alert')}${result.ready ? '可以发布' : '待调整'}</h4>
            ${metrics([['students','user','学生',result.student_count], ['groups','users','小组',result.group_count],
                ['topics','layers','可派题目',result.eligible_topic_count]])}
            ${sizes ? `<p class="pt-meta">${h(sizes)}${result.unassigned_count ? ` · ${h(result.unassigned_count)} 人待入组` : ''}</p>` : ''}
            ${issues(result.issues)}${publicCopies.length ? disclosure('本班已有公开副本', issues(publicCopies), true, {icon:'triangle-alert'}) : ''}
            ${result.ready ? button('确认发布', 'publish', { variant: 'prominent' }) : ''}</section>`;
        q('[data-pt-preview]').scrollIntoView({ block: 'nearest' });
    }
    async function publish() {
        if (!preview) return;
        const signature = JSON.stringify(preview.payload);
        if (publishIntent?.signature !== signature) publishIntent = { signature, id: newId() };
        try {
            activity = (await post('/activities', { ...preview.payload, preview_token: preview.token, request_id: publishIntent.id,
                ...(draftRevision ? { draft_revision: draftRevision } : {}) })).activity;
        } catch (error) {
            if (error.status === 409) {
                preview = null;
                q('[data-pt-preview]').textContent = '数据已变更，请重新核对发布条件。';
            }
            if (error.status == null || error.status >= 500) {
                throw new Error('未收到回执，请再点一次「确认发布」取回结果。');
            }
            throw error;
        }
        displayActivity();
        refreshAfterPublish = refreshGroups().catch(() => showToast('派题已保存，刷新协作区可查看最新名单。', 'warning'));
    }

    function displayActivity() {
        const a = activity;
        const path = `${base}/activities/${encodeURIComponent(a.id)}`;
        const group = a.my_group, topic = a.my_topic;
        const common = a.common?.length ? disclosure('公共说明', fileLinks(`${path}/files`, a.common), !teacher && !topic, {icon:'info'}) : '';
        const assignment = a.assignment_id ? linkButton(`去提交 · ${a.assignment_title || '关联作业'}`,
            `/assignment/${encodeURIComponent(a.assignment_id)}`, {icon:'external-link', variant:'glass'}) : '';
        if (!teacher) {
            const context = `<p class="pt-context">${iconMarkup('users')}<span>${group ? h(group.name) + ' · ' : ''}${h(a.title)}</span></p>`;
            const join = !group && a.state === 'published' && findJoinButton(a.scheme_id);
            const task = group && topic ? `<div class="pt-title-group">${context}
                <h3 class="pt-title">${h(topic.number ? topic.number + ' · ' : '')}${h(topic.title)}</h3></div>
                ${topicBody(topic, topicFiles(base, a.id, topic.key), {student:true})}` :
                `${context}<p class="pt-empty">${iconMarkup('book-open')}本次项目尚无分配给你的题目</p>${join ? button('前往随机入组', 'go-join', {icon:'shuffle', variant:'prominent'}) : ''}`;
            draw(`<section class="pt-stack pt-student">${task}${assignment}${common}</section>`, canReturn, a.state === 'archived' ? '本组题目 · 已归档' : '本组题目');
            return;
        }
        const rows = a.groups.map(g => `<details class="pt-disclosure pt-group-row"><summary class="lq-disclosure-trigger" data-lq-component="disclosure">
            <span class="pt-group-id">${iconMarkup('users')}${h(g.name)}</span><strong>${h(topicName(g.topic))}</strong>
            <small class="pt-meta">${h(g.member_count)} 人</small>${iconMarkup('chevron-down')}</summary>
            <div class="pt-stack">${topicBody(g.topic, topicFiles(base, a.id, g.topic.key))}</div></details>`).join('');
        draw(`<section class="pt-stack"><div class="pt-title-group"><small class="pt-context" role="status">${iconMarkup(a.state === 'archived' ? 'archive' : a.state === 'withdrawn' ? 'ban' : 'circle-check')}${h(activityStates[a.state])}</small>
            <h3 class="pt-title">${h(a.title)}</h3></div>
            ${metrics([['groups','users','小组',a.groups.length], ['students','user','已入组',a.groups.reduce((n,g) => n + g.member_count, 0)],
                ['pending','clock','待入组',a.unassigned_count]])}
            <div class="pt-actions">${linkButton('导出 Excel', `${path}/export.xlsx`, {icon:'download', download:true})}
            ${linkButton('CSV', `${path}/export.csv`, {icon:'download', variant:'ghost', download:true})}
            ${button('调整派题', 'manage', {icon:'settings', iconOnly:true})}</div>
            <div class="pt-group-list">${rows}</div>${common}${assignment}</section>`, true, '派题结果');
    }
    async function showActivity(id) {
        activity = (await request(`/activities/${encodeURIComponent(id)}`)).activity;
        displayActivity();
    }
    async function manage() {
        const a = activity;
        const [source, catalog] = await Promise.all([request(`/packages/${a.package_version_id}`), request('/packages')]);
        pkg = source.package;
        packages = catalog.packages;
        const records = a.history || a.audit || [];
        const correctionOptions = [['rebind', '更正组题对应'], ['upgrade', '更新题包版本']];
        const options = a.state === 'published' ? [...correctionOptions, ['archive', '归档，学生可继续回看'], ['withdraw', '撤回并停止学生阅读']] :
            a.state === 'archived' ? [['withdraw', '撤回并停止学生阅读']] :
                [['restore', '恢复原活动的可见性'], ...(records.some(r => r.action === 'archive') ? [] : correctionOptions)];
        draw(`<section class="pt-stack"><h3 class="pt-title">${h(a.title)}</h3>
            <form class="pt-stack" data-pt-form="change">${field('变更类型', select('action', options))}
            <div class="pt-stack" data-pt-change-fields></div>${field('变更原因', textarea('reason', '', 'maxlength="600"'))}
            <div data-pt-change-conflict></div>
            ${button('保存变更', 'change', { variant: 'prominent', submit: true })}</form>
            ${records.length ? disclosure('变更记录', `<ul>${records.map(r => `<li>${h(r.created_at)} · ${h(({ publish:'发布', archive:'归档', withdraw:'撤回', restore:'重新发布', upgrade:'更新版本', rebind:'更正绑定' })[r.action] || r.action)} ${h(r.reason)}</li>`).join('')}</ul>`, false, {icon:'clock'}) : ''}</section>`, true, '调整派题', 'activity');
        changeFields();
    }
    function changeFields() {
        const form = q('[data-pt-form="change"]');
        const kind = form.elements.action.value;
        const holder = q('[data-pt-change-fields]');
        q('[data-pt-change-conflict]').replaceChildren();
        form.elements.reason.required = kind !== 'archive';
        if (kind === 'rebind') {
            const options = pkg.topics.filter(t => activity.pool_topic_keys.includes(t.key)).map(t => [t.key, topicName(t)]);
            replace(holder, field('小组', select('group_id', activity.groups.map(g => [g.id, `${g.name} · ${topicName(g.topic)}`]))) +
                field('新题目', select('new_topic', [['', '选择题目'], ...options], '', 'required')) + '<p data-pt-swap aria-live="polite"></p>');
        } else if (kind === 'upgrade') {
            const versions = packages.filter(p => p.package_key === pkg.package_key && p.id !== pkg.id && p.mapping_confirmed !== false);
            replace(holder, field('目标版本', select('package_version_id', [['', '选择新版本'], ...versions.map(p => [p.id, versionLabel(p)])], '', 'required')) +
                (versions.length ? `${button('预览新版任务书', 'compare-version')}<div data-pt-upgrade></div>` : '<p>请先导入同一题包的新版本。</p>'));
        } else replace(holder, '');
    }
    async function compareVersion() {
        const id = q('form').elements.package_version_id.value;
        if (!id) throw new Error('请先选择要更新到的版本。');
        const next = (await request(`/packages/${encodeURIComponent(id)}`)).package;
        const holder = q('[data-pt-upgrade]');
        holder.innerHTML = `<p>从 ${h(versionLabel(pkg))} 更新至 ${h(versionLabel(next))}。请核对已派给学生的题目。</p>
            ${issues(next.common_issues)}${activity.groups.map(group => {
                const topic = next.topics.find(t => t.key === group.topic.key);
                return disclosure(`${group.name} · ${topic ? topicName(topic) : '新版本缺少原题'}`, topic
                    ? `<p>${h(readiness[topic.readiness])}</p>${topicBody(topic, pkgFiles(base, next.id))}${issues(topic.issues)}`
                    : `<p>原题为 ${h(topicName(group.topic))}</p>`);
            }).join('')}`;
        holder.dataset.version = id;
    }
    function rebindings(form) {
        const group = activity.groups.find(g => String(g.id) === form.elements.group_id.value);
        const key = form.elements.new_topic.value;
        if (!key || !group || group.topic.key === key) return [];
        const other = activity.groups.find(g => g.topic.key === key);
        return [{ group_id: group.id, topic_key: key }, ...(other ? [{ group_id: other.id, topic_key: group.topic.key }] : [])];
    }
    async function change(form) {
        const values = new FormData(form);
        const action = values.get('action');
        const data = { action, reason: values.get('reason').trim(), revision: activity.revision };
        if (action === 'rebind') {
            data.bindings = rebindings(form);
            if (!data.bindings.length) throw new Error('请选择与原题不同的题目。');
        }
        if (action === 'upgrade') {
            if (q('[data-pt-upgrade]')?.dataset.version !== values.get('package_version_id')) throw new Error('请先预览新版任务书，核对各组的内容。');
            data.package_version_id = Number(values.get('package_version_id'));
        }
        if (form.elements.acknowledge_work?.checked) data.acknowledge_work = true;
        try {
            activity = (await post(`/activities/${activity.id}/change`, data)).activity;
        } catch (error) {
            if (error.status === 409) {
                q('[data-pt-change-conflict]').innerHTML = error.message.includes('学生作品或提交')
                    ? check('acknowledge_work', '1', '已核对受影响组的现有作品和提交，确认本次换题')
                    : button('读取最新活动并重新核对', 'reload-change');
            }
            throw error;
        }
        displayActivity();
        showToast('派题活动已更新', 'success');
    }

    body.addEventListener('input', event => {
        if (!event.target.closest('form')) return;
        dirty = true;
        if (event.target.closest('[data-pt-form="publish"]') && preview) {
            preview = null;
            q('[data-pt-preview]').textContent = '配置已调整，请重新核对发布条件。';
        }
    });
    body.addEventListener('change', event => {
        const el = event.target;
        const form = el.closest('form');
        if (!form) return;
        if (form.dataset.ptForm === 'import' && el.type === 'file') {
            if (el.files.length) form.elements[el.name === 'zip' ? 'folder' : 'zip'].value = '';
            const files = form.elements.zip.files.length ? form.elements.zip.files : form.elements.folder.files;
            const folder = files[0]?.webkitRelativePath?.split('/')[0];
            form.querySelector('[data-pt-upload-options]').hidden = !files.length;
            form.querySelector('output').textContent = folder ? `${folder} · ${files.length} 个文件` : files[0]?.name || '';
        } else if (form.dataset.ptForm === 'mapping' && el.name === 'edit_index') {
            updateManifest(form, manifest, editIndex);
            editIndex = Number(el.value);
            renderEditor();
            q('[name="edit_index"]')?.focus();
        } else if (form.dataset.ptForm === 'publish' && ['scheme', 'group_ids', 'min_members', 'group_count', 'max_members', 'mode', 'topic_keys', 'allow_brief'].includes(el.name)) {
            if (['scheme', 'group_count', 'max_members', 'min_members'].includes(el.name)) autoPlan = null;
            if (el.name === 'allow_brief') {
                pkg.topics.forEach((topic, i) => {
                    if (topic.readiness !== 'brief') return;
                    const box = form.querySelectorAll('[name="topic_keys"]')[i];
                    box.disabled = !el.checked;
                    box.checked = el.checked && topic.pool !== 'reserve';
                });
            }
            updateBindings(form);
        } else if (form.dataset.ptForm === 'change') {
            if (el.name === 'action') changeFields();
            else if (el.name === 'package_version_id' && q('[data-pt-upgrade]')) {
                q('[data-pt-upgrade]').replaceChildren();
                delete q('[data-pt-upgrade]').dataset.version;
            }
            else if (['group_id', 'new_topic'].includes(el.name)) {
                q('[data-pt-change-conflict]').replaceChildren();
                const summary = rebindings(form).map(b =>
                    `${activity.groups.find(g => g.id === b.group_id).name} → ${topicName(pkg.topics.find(t => t.key === b.topic_key))}`);
                q('[data-pt-swap]').textContent = summary.join('；');
            }
        }
    });
    body.addEventListener('submit', event => {
        const form = event.target;
        event.preventDefault();
        if (form.dataset.ptForm === 'import') void task(() => importPackage(form), true);
        if (form.dataset.ptForm === 'mapping') void task(async () => {
            updateManifest(form, manifest, editIndex);
            displayPackage((await post(`/packages/${pkg.id}/map`, { manifest })).package);
            showToast('题包映射已保存', 'success');
        }, true);
        if (form.dataset.ptForm === 'publish') void task(() => preflight(form));
        if (form.dataset.ptForm === 'change') void task(() => change(form), true);
    });
    root.addEventListener('click', event => {
        const target = event.target.closest('[data-pt-action]');
        if (!target || target.type === 'submit') return;
        const action = target.dataset.ptAction;
        void task(async () => {
            if (action === 'retry') return home(true);
            if (action === 'go-join') {
                const join = findJoinButton(activity.scheme_id);
                if (!join) return showActivity(activity.id);
                if (await getLayerSystem(document).close(handle, 'button')) {
                    join.scrollIntoView({block: 'center'});
                    join.focus({preventScroll: true});
                }
                return;
            }
            if (action === 'home' && await discard()) {
                if (returnTo === 'package') displayPackage(pkg);
                else if (returnTo === 'activity') displayActivity();
                else await home();
            }
            if (action === 'package') displayPackage((await request(`/packages/${target.dataset.id}`)).package);
            if (action === 'edit') startMapping();
            if (action === 'manifest') downloadManifest(pkg.manifest);
            if (action === 'add-topic' || action === 'remove-topic') {
                const form = q('[data-pt-form="mapping"]');
                updateManifest(form, manifest, editIndex);
                if (action === 'add-topic') {
                    manifest.topics.push({ key: newId(), number: '', title: '', brief: '', entry: '', pool: 'regular', attachments: [] });
                    editIndex = manifest.topics.length - 1;
                } else {
                    manifest.topics.splice(editIndex, 1);
                    editIndex = Math.max(0, editIndex - 1);
                }
                dirty = true;
                renderEditor();
            }
            if (action === 'prepare') await prepare();
            if (action === 'save-draft') await saveDraft();
            if (action === 'reload-draft' && await discard()) await prepare();
            if (action === 'publish') await publish();
            if (action === 'activity') await showActivity(target.dataset.id);
            if (action === 'manage') await manage();
            if (action === 'compare-version') await compareVersion();
            if (action === 'reload-change' && await discard()) await showActivity(activity.id);
        }, action === 'publish' || action === 'save-draft');
    });
    draw('<p role="status" data-pt-loading>正在读取课堂项目…</p>', false);
    void task(() => home(true));
}

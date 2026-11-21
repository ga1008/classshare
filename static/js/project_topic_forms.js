import { componentMarkup } from './lq/components.js';
import { attributesMarkup, escapeHtml as h, normalizeAttributes, safeUrl } from './lq/html.js';
import { iconMarkup } from './lq/icons.js';

export const readiness = { complete: '完整任务书', brief: '仅简介', missing: '待补材料' };
export const activityStates = { published: '已发布', archived: '已归档', withdrawn: '已撤回' };

const actionIcons = {
    home: 'arrow-left', activity: 'book-open', package: 'folder-open', import: 'upload', edit: 'pencil',
    manifest: 'download', prepare: 'send', manage: 'settings', preview: 'list', publish: 'send', change: 'check',
    'add-topic': 'plus', 'remove-topic': 'trash-2', 'save-map': 'check', 'save-draft': 'file-text',
    'compare-version': 'list', 'reload-change': 'refresh-cw', 'reload-draft': 'refresh-cw',
};
const stateIcons = { complete: 'circle-check', brief: 'file-text', missing: 'circle-alert' };

export function button(label, action, { id, variant = 'glass', submit = false, disabled = false,
    icon = actionIcons[action], iconOnly = false } = {}) {
    return componentMarkup('button', { label: iconOnly ? '' : label, icon, type: submit ? 'submit' : 'button',
        variant, size: 'sm', disabled, attrs: { 'data-pt-action': action, 'data-id': id,
            ...(iconOnly ? { 'aria-label': label, title: label } : {}) } });
}

export function linkButton(label, href, { icon, iconOnly = false, variant = 'glass', download = false, newTab = false } = {}) {
    // The native download attribute belongs to the link, including keyboard activation.
    const attrs = { href: safeUrl(href), ...(newTab ? { target: '_blank', rel: 'noopener noreferrer' } : {}),
        ...(iconOnly ? { 'aria-label': label, title: label } : {}) };
    return `<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--${h(variant)}${iconOnly ? ' lq-btn--icon' : ''}"${attributesMarkup(attrs)}${download ? ' download' : ''}>${icon ? `<span class="lq-btn__icon" aria-hidden="true">${iconMarkup(icon)}</span>` : ''}${iconOnly ? '' : `<span class="lq-btn__label">${h(label)}</span>`}</a>`;
}

export function field(label, control) {
    return `<label class="lq-field"><span class="lq-field__label">${h(label)}</span>${control}</label>`;
}

export function input(name, value = '', attrs = '') {
    return `<input data-lq-component="input" class="lq-input" name="${h(name)}" value="${h(value)}" ${attrs}>`;
}

export function textarea(name, value = '', attrs = '') {
    return `<textarea data-lq-component="textarea" class="lq-textarea" name="${h(name)}" rows="3" ${attrs}>${h(value)}</textarea>`;
}

export function select(name, options, selected = '', attrs = '') {
    const values = new Set([].concat(selected ?? []).map(String));
    return `<select data-lq-dropdown data-lq-component="select" class="lq-select" name="${h(name)}" ${attrs}>${options.map(([value, label]) =>
        `<option value="${h(value)}"${values.has(String(value)) ? ' selected' : ''}>${h(label)}</option>`).join('')}</select>`;
}

export function check(name, value, label, { checked = false, disabled = false, detail = '' } = {}) {
    return `<label class="pt-check"><input type="checkbox" data-lq-component="checkbox" class="lq-checkbox" name="${h(name)}" value="${h(value)}"${checked ? ' checked' : ''}${disabled ? ' disabled' : ''}><span class="pt-topic-option-body">${h(label)}${detail ? ` <small class="pt-meta">${h(detail)}</small>` : ''}</span></label>`;
}

export function disclosure(label, content, open = false, { icon, meta = '', attrs = {}, metaAttrs = {} } = {}) {
    return `<details class="pt-disclosure"${attributesMarkup(normalizeAttributes(attrs))}${open ? ' open' : ''}>
        <summary class="lq-disclosure-trigger" data-lq-component="disclosure"><span class="pt-summary-label">${icon ? iconMarkup(icon) : ''}<span>${h(label)}</span></span>${meta ? ` <small class="pt-summary-meta"${attributesMarkup(normalizeAttributes(metaAttrs))}>${h(meta)}</small>` : ''}</summary>
        ${content}</details>`;
}

export function issues(items = []) {
    return items.length ? `<ul class="pt-issues">${items.map(message => `<li>${h(message)}</li>`).join('')}</ul>` : '';
}

function fileUrl(base, path) {
    return `${base}/${path.split('/').map(encodeURIComponent).join('/')}`;
}

function downloadLink(base, path) {
    return linkButton(`下载 ${path}`, `${fileUrl(base, path)}?download=1`,
        { icon: 'download', iconOnly: true, download: true, variant: 'ghost' });
}

export function fileLinks(base, paths = []) {
    if (!paths.length) return '';
    const usedNames = new Set();
    const duplicates = new Set();
    for (const path of paths) {
        const name = path.split('/').pop();
        if (usedNames.has(name)) duplicates.add(name);
        usedNames.add(name);
    }
    return `<ul class="pt-files">${paths.map(path => {
        const name = path.split('/').pop();
        return `<li><a class="pt-file-label" href="${h(fileUrl(base, path))}" target="_blank" rel="noopener noreferrer" aria-label="${h(path)}" title="${h(path)}">${iconMarkup('file-text')}<span>${h(duplicates.has(name) ? path : name)}</span></a>${downloadLink(base, path)}</li>`;
    }).join('')}</ul>`;
}

function pathOptions(pkg, paths) {
    const uploaded = new Set(pkg.files.map(f => f.path));
    return [...new Set([...paths.filter(Boolean), ...uploaded])].map(p => [p, uploaded.has(p) ? p : `${p}（缺失）`]);
}

export function packageOverview(pkg, base) {
    const totals = Object.entries(readiness).map(([type, label]) => {
        const count = pkg.topics.filter(t => t.readiness === type).length;
        return count ? `<li>${iconMarkup(stateIcons[type])}<span><strong>${count}</strong> 道${h(label)}</span></li>` : '';
    }).join('');
    return `${totals ? `<ul class="pt-totals" data-pt-totals aria-label="题目就绪情况">${totals}</ul>` : '<p>尚未添加题目</p>'}
        ${pkg.mapping_confirmed === false ? '<p role="status">请先确认每道题的文件。</p>' : ''}
        ${pkg.issues?.length ? disclosure('导入提示', issues(pkg.issues), false, { icon: 'triangle-alert', meta: `${pkg.issues.length} 项` }) : ''}
        ${pkg.common.length ? disclosure('公共说明', fileLinks(base, pkg.common), false, { icon: 'users', meta: `${pkg.common.length} 份` }) : ''}
        <div class="pt-stack">${pkg.topics.map(t => disclosure(topicName(t), topicBody(t, base) + issues(t.issues), false, {
            icon: stateIcons[t.readiness], meta: [t.pool === 'reserve' ? '备选' : '', t.readiness === 'complete' ? '' : readiness[t.readiness]].filter(Boolean).join(' · '),
        })).join('')}</div>`;
}

export function topicName(topic) {
    return [topic.number, topic.title].filter(Boolean).join(' · ') || '未命名题目';
}

export function topicBody(topic, base, { student = false } = {}) {
    const brief = topic.brief ? `<p class="pt-brief">${h(topic.brief)}</p>` : '';
    const attachments = topic.attachments || [];
    if (!student) return brief + fileLinks(base, [topic.entry, ...attachments].filter(Boolean));
    const entry = topic.entry ? `<div class="pt-file-actions">${linkButton('打开任务书', fileUrl(base, topic.entry),
        { variant: 'prominent', icon: 'book-open', newTab: true })}${downloadLink(base, topic.entry)}</div>` : '';
    return brief + entry + (attachments.length ? disclosure('附件', fileLinks(base, attachments), true, { icon: 'paperclip', meta: `${attachments.length} 份` }) : '');
}

export function mappingForm(pkg, manifest) {
    const settings = `<div class="pt-stack"><div class="pt-grid">
        ${field('题包名称', input('title', manifest.title || pkg.title, 'required maxlength="160"'))}
        ${field('版本', input('version', manifest.version, 'required'))}</div>
        ${field('题包标识', input('packageKey', manifest.packageKey, 'required'))}
        ${field('全班可见的文件', select('common', pathOptions(pkg, manifest.common), manifest.common, 'multiple data-lq-searchable'))}</div>`;
    return `<form data-pt-form="mapping" class="pt-stack">
        <div data-pt-edit-slot></div>
        ${disclosure('题包设置与公共文件', settings, false, { icon: 'settings' })}
        ${button('确认并保存', 'save-map', { variant: 'prominent', submit: true })}
        </form>`;
}

export function topicEditor(pkg, manifest, index) {
    const current = manifest.topics[index];
    if (!current) return button('添加第一道题', 'add-topic');
    return `<div class="pt-stack">
        <div class="pt-row">${field('当前题目', select('edit_index', manifest.topics.map((t, i) => [i, topicName(t)]), index, 'data-lq-searchable'))}
        <div class="pt-actions">${button('添加题目', 'add-topic', { iconOnly: true })}
        ${button('移除当前题目', 'remove-topic', { iconOnly: true, variant: 'ghost' })}</div></div>
        <div class="pt-grid">${field('题号', input('number', current.number))}${field('题目名称', input('topic_title', current.title, 'required'))}</div>
        ${field('任务书', select('entry', [['', '仅发简介'], ...pathOptions(pkg, [current.entry])], current.entry, 'data-lq-searchable'))}
        ${field('简介', textarea('brief', current.brief))}
        ${field('附件', select('attachments', pathOptions(pkg, current.attachments || []), current.attachments || [], 'multiple data-lq-searchable'))}
        ${disclosure('题目标识与题池', `<div class="pt-grid">${field('题目标识', input('key', current.key, 'required'))}
        ${field('题池', select('pool', [['regular', '主选'], ['reserve', '备选']], current.pool || 'regular'))}</div>`, false, { icon: 'settings' })}
        </div>`;
}

export function updateManifest(form, manifest, index) {
    const values = new FormData(form);
    Object.assign(manifest, { title: values.get('title').trim(), packageKey: values.get('packageKey').trim(),
        version: values.get('version').trim(), common: values.getAll('common') });
    const topic = manifest.topics[index];
    if (topic) Object.assign(topic, { key: values.get('key').trim(), number: values.get('number').trim(),
        title: values.get('topic_title').trim(), brief: values.get('brief').trim(),
        entry: values.get('entry'), attachments: values.getAll('attachments'), pool: values.get('pool') });
}

export function publishForm(pkg, snapshot) {
    const schemes = snapshot.schemes || [];
    const schemeOptions = [...schemes.map(s => [s.id, `${s.name} · ${s.group_count} 组${s.is_history ? '（已归档）' : ''}`]), ['new', '新建随机分组']];
    const selected = pkg.topics.filter(t => t.pool !== 'reserve' && t.readiness === 'complete').length;
    const topics = pkg.topics.map(t => {
        const detail = [t.pool === 'reserve' ? '备选' : '', t.readiness === 'complete' ? '' : readiness[t.readiness]].filter(Boolean).join(' · ');
        return check('topic_keys', t.key, topicName(t), { checked: t.pool !== 'reserve' && t.readiness === 'complete', disabled: t.readiness !== 'complete', detail });
    }).join('');
    const pool = `<div class="pt-stack"><div${pkg.topics.some(t => t.readiness === 'brief') ? '' : ' hidden'}>
        ${check('allow_brief', '1', '允许只发简介')}</div>
        <fieldset class="pt-topic-options"><legend class="sr-only">选择派发的题目</legend>${topics || '<p>暂无题目</p>'}</fieldset></div>`;
    return `<form data-pt-form="publish" class="pt-stack">
        <div class="pt-grid">${field('活动名称', input('title', pkg.title.slice(0, 100), 'required maxlength="100"'))}
        ${field('分组方案', select('scheme', schemeOptions, schemes[0]?.id ?? 'new'))}</div>
        <div data-pt-participants></div>
        <div class="pt-stack" data-pt-new>
        <div class="pt-grid">${field('方案名称', input('scheme_name', pkg.title.slice(0, 60), 'maxlength="60"'))}
        ${field('学生入组', select('allocation_mode', [['all', '随机分配全班'], ['self', '学生自行抽组']], 'all'))}</div>
        <div class="pt-grid pt-bounds">${field('每组最少人数', input('min_members', 2, 'type="number" min="1" max="12" required'))}
        ${field('每组最多人数', input('max_members', 5, 'type="number" min="1" max="12" required'))}
        ${field('组数', input('group_count', '', 'type="number" min="1" max="60" placeholder="自动"'))}</div>
        </div>
        <fieldset class="pt-mode"><legend class="lq-field__label">派题方式</legend><div class="pt-actions">
        <label class="lq-choice"><input class="lq-radio" data-lq-component="radio" type="radio" name="mode" value="random" checked>${iconMarkup('shuffle')}<span class="lq-choice__text">随机派题</span></label>
        <label class="lq-choice"><input class="lq-radio" data-lq-component="radio" type="radio" name="mode" value="manual">${iconMarkup('pencil')}<span class="lq-choice__text">逐组指定</span></label>
        </div></fieldset>
        ${disclosure('选择题目', pool, selected === 0, { icon: 'list', attrs: {'data-pt-pool': ''},
            meta: `已选 ${selected} / ${pkg.topics.length}`, metaAttrs: {'data-pt-pool-count': ''} })}
        <div data-pt-bindings></div>
        ${snapshot.assignments?.length ? disclosure('关联作业', field('提交入口', select('assignment_id', [['', '不关联'], ...snapshot.assignments.map(a => [a.id, a.title])])), false, { icon: 'link' }) : ''}
        <div class="pt-actions">${button('保存草稿', 'save-draft')}${button('预览派题', 'preview', { submit: true, variant: 'prominent' })}</div>
        <div data-pt-draft-status role="status"></div>
        <div data-pt-preview aria-live="polite"></div>
        </form>`;
}

export function bindingFields(groups, options, previous = {}) {
    return groups.map(g => {
        const reference = g.id ?? g.group_index;
        return field(g.name || `第 ${g.group_index} 组`, select(`binding-${reference}`, [['', '选择一道题'], ...options], previous[reference] || '', 'required data-lq-searchable'));
    }).join('');
}

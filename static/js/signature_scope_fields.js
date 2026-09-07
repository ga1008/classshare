import { escapeHtml } from './ui.js';
import { apiFetch } from './api.js';

export const signatureScopeOptions = [
    { value: 'platform', label: '平台可见' },
    { value: 'school', label: '学校可见' },
    { value: 'college', label: '学院可见' },
    { value: 'department', label: '系部可见' },
    { value: 'personal', label: '个人可见' },
];
const scopeHelp = {
    platform: '平台内所有账号可见。个人签名仍须本人或归属人批准后使用。',
    school: '所选学校及其下属学院、系部人员可见。可见不代表已获使用授权。',
    college: '所选学院及其下属系部人员可见。可见不代表已获使用授权。',
    department: '所选系部人员可见。可见不代表已获使用授权。',
    personal: '仅签名归属人、签名者本人及超级管理员可见。',
};
const orgKeys = ['school_code', 'school_name', 'college', 'department'];
const orgValue = (value = {}) => Object.fromEntries(orgKeys.map(key => [key, String(value[key] || '').trim()]));
const orgIdentity = value => [value.school_code, value.college, value.department].join('\u001f');
const matchesScope = (member, org, scope) => member.school_code === org.school_code
    && (!['college', 'department'].includes(scope) || member.college === org.college)
    && (scope !== 'department' || member.department === org.department);
let scopeControlId = 0;
let organizationTreeRequest = null;

function loadOrganizationTree() {
    if (!organizationTreeRequest) {
        organizationTreeRequest = apiFetch('/api/manage/system/organizations/tree', { silent: true })
            .catch(error => { organizationTreeRequest = null; throw error; });
    }
    return organizationTreeRequest;
}

export class SignatureScopeFields {
    constructor({ root, actor = {}, options = [], schools = [], value = {} }) {
        this.root = root;
        this.actor = actor;
        this.schools = schools;
        this.id = `signature-scope-${++scopeControlId}`;
        this.options = options.length ? options : signatureScopeOptions;
        this.scope = value.scope_level || 'personal';
        const memberships = Array.isArray(actor.memberships) ? actor.memberships : [actor];
        this.org = orgValue(value.school_code ? value : (memberships[0] || {}));
        this.memberships = memberships.map(orgValue)
            .filter(item => item.school_code)
            .filter((item, index, items) => items.findIndex(other => orgIdentity(item) === orgIdentity(other)) === index);
        this.render();
        if (this.isAdmin()) this.loadDirectory();
    }

    isAdmin() { return Boolean(this.actor.is_super_admin); }

    render() {
        this.root.classList.add('signature-scope-control');
        this.root.innerHTML = `
            <label class="signature-scope-field"><span>可见范围</span>
                <select data-scope-level aria-describedby="${this.id}-help">${this.options.map(option => `<option value="${escapeHtml(option.value)}" ${option.value === this.scope ? 'selected' : ''}>${escapeHtml(option.label)}</option>`).join('')}</select>
            </label>
            <label class="signature-scope-field" data-scope-membership-field><span>共享到的组织</span>
                <select data-scope-membership aria-label="共享到的组织"></select>
            </label>
            <div class="signature-scope-org" data-scope-admin-org>
                <label class="signature-scope-field"><span>学校</span><input data-scope-school list="${this.id}-schools" placeholder="选择学校或输入学校代码" autocomplete="off"></label>
                <datalist id="${this.id}-schools" data-scope-school-options>${this.schools.map(school => `<option value="${escapeHtml(`${school.school_name || school.school_code}（${school.school_code}）`)}"></option>`).join('')}</datalist>
                <label class="signature-scope-field" data-scope-college-field><span>学院</span><input data-scope-college list="${this.id}-colleges" maxlength="120" placeholder="选择所属学院"><datalist id="${this.id}-colleges" data-scope-college-options></datalist></label>
                <label class="signature-scope-field" data-scope-department-field><span>系部</span><input data-scope-department list="${this.id}-departments" maxlength="120" placeholder="选择所属系部"><datalist id="${this.id}-departments" data-scope-department-options></datalist></label>
            </div>
            <p id="${this.id}-help" class="signature-scope-help" data-scope-help></p><p class="signature-scope-help" data-scope-directory-status hidden></p>`;
        this.root.querySelector('[data-scope-school]').value = this.org.school_name
            ? `${this.org.school_name}（${this.org.school_code}）` : this.org.school_code;
        this.root.querySelector('[data-scope-college]').value = this.org.college;
        this.root.querySelector('[data-scope-department]').value = this.org.department;
        this.root.querySelector('[data-scope-level]').addEventListener('change', event => {
            this.scope = event.target.value;
            this.sync();
        });
        this.root.querySelector('[data-scope-membership]').addEventListener('change', event => {
            const membership = this.memberships[Number(event.target.value)];
            if (membership) this.org = orgValue(membership);
        });
        this.root.querySelector('[data-scope-school]').addEventListener('input', () => this.syncDirectoryOptions());
        this.root.querySelector('[data-scope-college]').addEventListener('input', () => this.syncDirectoryOptions());
        this.sync();
    }

    sync() {
        const anchored = ['school', 'college', 'department'].includes(this.scope);
        const needsCollege = ['college', 'department'].includes(this.scope);
        const needsDepartment = this.scope === 'department';
        this.root.querySelector('[data-scope-membership-field]').hidden = !anchored || this.isAdmin();
        this.root.querySelector('[data-scope-admin-org]').hidden = !anchored || !this.isAdmin();
        this.root.querySelector('[data-scope-college-field]').hidden = !needsCollege;
        this.root.querySelector('[data-scope-department-field]').hidden = !needsDepartment;
        const select = this.root.querySelector('[data-scope-membership]');
        const eligible = this.memberships.map((org, index) => ({ org, index }))
            .filter(({ org }) => org.school_code && (!needsCollege || org.college) && (!needsDepartment || org.department));
        const selectedIndex = eligible.find(({ org }) => matchesScope(org, this.org, this.scope))?.index;
        const missingCurrent = this.org.school_code && selectedIndex == null;
        select.innerHTML = eligible.length ? `${missingCurrent ? '<option value="" selected>原归属已失效，请选择当前有效组织</option>' : ''}${eligible.map(({ org, index }) => `<option value="${index}" ${index === selectedIndex ? 'selected' : ''}>${escapeHtml([org.school_name || org.school_code, needsCollege ? org.college : '', needsDepartment ? org.department : ''].filter(Boolean).join(' / '))}</option>`).join('')}` : '<option value="">暂无符合该范围的组织归属</option>';
        if (!this.isAdmin() && anchored && select.value !== '') this.org = orgValue(this.memberships[Number(select.value)]);
        this.root.querySelector('[data-scope-help]').textContent = scopeHelp[this.scope] || '';
    }

    async loadDirectory() {
        const status = this.root.querySelector('[data-scope-directory-status]');
        status.hidden = false;
        status.textContent = '正在读取学校、学院和系部目录…';
        try {
            const data = await loadOrganizationTree();
            this.directory = data.schools || [];
            this.schools = this.directory;
            this.root.querySelector('[data-scope-school-options]').innerHTML = this.schools.map(school => `<option value="${escapeHtml(`${school.school_name || school.school_code}（${school.school_code}）`)}"></option>`).join('');
            this.syncDirectoryOptions();
            status.hidden = true;
        } catch {
            status.textContent = '组织目录暂时加载失败；可填写准确的学校代码、学院和系部名称。';
        }
    }

    syncDirectoryOptions() {
        if (!this.directory) return;
        const rawSchool = this.root.querySelector('[data-scope-school]').value.trim();
        const school = this.directory.find(item => rawSchool === item.school_code || rawSchool === `${item.school_name || item.school_code}（${item.school_code}）`);
        const colleges = school?.colleges || [];
        this.root.querySelector('[data-scope-college-options]').innerHTML = colleges.map(item => `<option value="${escapeHtml(item.college_name)}"></option>`).join('');
        const collegeName = this.root.querySelector('[data-scope-college]').value.trim();
        const departments = colleges.find(item => item.college_name === collegeName)?.departments || [];
        this.root.querySelector('[data-scope-department-options]').innerHTML = departments.map(item => `<option value="${escapeHtml(item.department_name)}"></option>`).join('');
    }

    getValue() {
        const scope = this.root.querySelector('[data-scope-level]').value;
        if (!signatureScopeOptions.some(option => option.value === scope)) throw new Error('请选择有效的签名可见范围。');
        const anchored = ['school', 'college', 'department'].includes(scope);
        let org = { ...this.org };
        if (this.isAdmin() && anchored) {
            const rawSchool = this.root.querySelector('[data-scope-school]').value.trim();
            const matched = this.schools.find(school => rawSchool === school.school_code || rawSchool === `${school.school_name || school.school_code}（${school.school_code}）`);
            const retained = rawSchool === `${this.org.school_name}（${this.org.school_code}）`;
            org.school_code = matched?.school_code || (retained ? this.org.school_code : (/^[a-z0-9_.-]+$/i.test(rawSchool) ? rawSchool : ''));
            org.college = this.root.querySelector('[data-scope-college]').value.trim();
            org.department = this.root.querySelector('[data-scope-department]').value.trim();
        }
        if (anchored && (!org.school_code || (!this.isAdmin() && this.root.querySelector('[data-scope-membership]').value === ''))) throw new Error('请选择该可见范围对应的有效组织。');
        if (['college', 'department'].includes(scope) && !org.college) throw new Error('学院或系部可见需要指定学院。');
        if (scope === 'department' && !org.department) throw new Error('系部可见需要指定系部。');
        return { scope_level: scope, school_code: org.school_code, college: org.college, department: org.department };
    }
}

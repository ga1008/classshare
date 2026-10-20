// Native inputs remain the only form values. Suggestions never replace a draft.
const requests = new Map();
const TTL = 30_000;

async function suggestions(params) {
    const key = new URLSearchParams(params).toString();
    const existing = requests.get(key);
    if (existing && existing.expires > Date.now()) return existing.promise;
    const promise = fetch(`/api/manage/organization-suggestions?${key}`, {
        credentials: 'same-origin', headers: { Accept: 'application/json' },
    }).then(async (response) => {
        if (!response.ok) throw new Error('推荐暂不可用，可直接输入。');
        return response.json();
    }).catch((error) => {
        requests.delete(key);
        throw error;
    });
    if (requests.size >= 64) requests.delete(requests.keys().next().value);
    requests.set(key, { promise, expires: Date.now() + TTL });
    return promise;
}

export function bindOrganizationSuggestions(form) {
    const school = form?.elements.namedItem('school_name');
    const college = form?.elements.namedItem('college');
    const department = form?.elements.namedItem('department');
    const code = form?.elements.namedItem('school_code');
    if (!school || !college || !department) return null;
    const hint = form.querySelector('[data-organization-hint]');
    let generation = 0;
    let timer;
    let composing = false;
    let schools = [];
    const controller = new AbortController();
    const lists = [school, college, department].map((input) => {
        const list = document.createElement('datalist');
        list.id = `${input.id}-suggestions`;
        form.append(list);
        input.setAttribute('list', list.id);
        input.autocomplete = 'off';
        return list;
    });
    const setOptions = (list, values, aliases = {}) => {
        list.replaceChildren(...values.map((value) => {
            const option = document.createElement('option');
            option.value = value;
            if (aliases[value]?.length) option.label = aliases[value].join(' / ');
            return option;
        }));
    };
    const refresh = async () => {
        const ticket = ++generation;
        const params = { school_code: code?.value || '', school_name: school.value.trim(), college: college.value.trim() };
        try {
            const data = await suggestions(params);
            if (ticket !== generation || controller.signal.aborted) return;
            schools = data.schools || [];
            setOptions(lists[0], schools.map((item) => (
                schools.filter((candidate) => candidate.school_name === item.school_name).length > 1
                    ? `${item.school_name}（${item.school_code}）` : item.school_name
            )));
            setOptions(lists[1], data.colleges || []);
            setOptions(lists[2], data.departments || [], data.department_aliases || {});
            const matches = schools.filter((item) => item.school_name === school.value.trim());
            if (code && !matches.some((item) => item.school_code === code.value)) {
                code.value = matches.length === 1 ? matches[0].school_code : '';
            }
            if (hint) hint.textContent = '可从推荐中选择，也可直接输入；常见系别简称会在保存时统一。';
        } catch {
            if (ticket !== generation || controller.signal.aborted) return;
            if (hint) hint.textContent = '推荐暂不可用，仍可直接输入并保存。';
        }
    };
    const schedule = (event) => {
        ++generation; // Invalidate in-flight results immediately, before debounce.
        clearTimeout(timer);
        if (event?.target === school) {
            if (code) code.value = '';
            if (!composing) {
                const selected = schools.find((item) => `${item.school_name}（${item.school_code}）` === school.value);
                if (selected) {
                    school.value = selected.school_name;
                    if (code) code.value = selected.school_code;
                }
            }
            const employee = form.elements.namedItem('employee_number');
            if (employee) employee.value = '';
            setOptions(lists[1], []);
        }
        setOptions(lists[2], []);
        if (!composing) timer = setTimeout(refresh, 220);
    };
    for (const input of [school, college]) {
        input.addEventListener('compositionstart', () => { composing = true; ++generation; clearTimeout(timer); }, { signal: controller.signal });
        input.addEventListener('compositionend', (event) => { composing = false; schedule(event); }, { signal: controller.signal });
        input.addEventListener('input', schedule, { signal: controller.signal });
    }
    form.addEventListener('focusin', (event) => {
        if ([school, college, department].includes(event.target) && !composing) refresh();
    }, { signal: controller.signal });
    return {
        refresh,
        destroy() {
            ++generation;
            clearTimeout(timer);
            controller.abort();
            lists.forEach((list) => list.remove());
            [school, college, department].forEach((input) => input.removeAttribute('list'));
        },
    };
}

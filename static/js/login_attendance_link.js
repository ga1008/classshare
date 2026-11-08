/** Strict, side-effect-free attendance URL intent shared by both classroom hosts. */
const safeId = value => /^[1-9]\d*$/.test(String(value || '')) && Number.isSafeInteger(Number(value));
export function resolveAttendanceLink(search) {
    const params = new URLSearchParams(search || '');
    const names = ['attendance_id', 'student_id', 'appeal_id', 'history_only'];
    if (!names.some(name => params.has(name))) return { kind: 'none' };
    const result = { kind: 'valid' };
    const historical = params.has('history_only');
    if (historical && (params.getAll('history_only').length !== 1 || params.get('history_only') !== '1' || params.has('session_id'))) return { kind: 'invalid' };
    if (historical) result.history_only = true;
    for (const name of [ ...(historical ? [] : ['session_id']), 'attendance_id', 'student_id', 'appeal_id']) {
        const values = params.getAll(name);
        if (!values.length && !['session_id', 'attendance_id'].includes(name)) continue;
        if (values.length !== 1 || !safeId(values[0])) return { kind: 'invalid' };
        result[name] = Number(values[0]);
    }
    if (result.appeal_id && !result.student_id) return { kind: 'invalid' };
    return result;
}

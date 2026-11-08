import { expect, it } from 'vitest';
import { resolveAttendanceLink } from '../../static/js/login_attendance_link.js';

it('attendance intent retains exact lesson, round, student and appeal ids', () => {
    expect(resolveAttendanceLink('?session_id=12&attendance_id=9&student_id=4&appeal_id=7')).toEqual({ kind: 'valid', session_id: 12, attendance_id: 9, student_id: 4, appeal_id: 7 });
    expect(resolveAttendanceLink('?session_id=12')).toEqual({ kind: 'none' });
});
it('merged history has an exact independent round and no current lesson substitution',()=>{
    expect(resolveAttendanceLink('?attendance_id=9&history_only=1&student_id=4')).toEqual({kind:'valid',attendance_id:9,history_only:true,student_id:4});
});
it.each(['?attendance_id=9', '?session_id=12&student_id=4', '?session_id=12&attendance_id=9&appeal_id=7',
    '?session_id=12&session_id=13&attendance_id=9', '?session_id=12&attendance_id=9&student_id=4&student_id=5',
    '?session_id=0&attendance_id=9', '?session_id=-1&attendance_id=9', '?session_id=1.1&attendance_id=9',
    '?session_id=9007199254740992&attendance_id=9', '?session_id=12&attendance_id=09', '?session_id=12&attendance_id=9&student_id=',
    '?history_only=1','?attendance_id=9&history_only=0','?attendance_id=9&history_only=1&history_only=1','?session_id=12&attendance_id=9&history_only=1'])('rejects ambiguous or foreign-shaped URL intent %s', search => {
    expect(resolveAttendanceLink(search)).toEqual({ kind: 'invalid' });
});

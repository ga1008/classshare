/** Shared public attendance choices; backend remains the permission/transition authority. */
export const ATTENDANCE_STATES = Object.freeze([
    ['PRESENT', '出勤'], ['ABSENT', '缺课'], ['SICK_LEAVE', '病假'],
    ['PERSONAL_LEAVE', '事假'], ['LATE', '迟到'], ['EARLY', '早退'],
].map(choice => Object.freeze(choice)));

// School-only values are displayed and can be corrected to a local teacher choice.
export const SCHOOL_ACTIVITY_LABELS = Object.freeze({ STUDY_TOUR: '研学', PRACTICE: '实践', VOLUNTEER: '志愿服务' });
export const RECORDED_ATTENDANCE_STATES = Object.freeze([...ATTENDANCE_STATES, ['LATE_OR_EARLY', '迟到或早退'], ...Object.entries(SCHOOL_ACTIVITY_LABELS)]);

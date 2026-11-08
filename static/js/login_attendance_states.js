/** Shared public attendance choices; backend remains the permission/transition authority. */
export const ATTENDANCE_STATES = Object.freeze([
    ['PRESENT', '出勤'], ['ABSENT', '缺课'], ['SICK_LEAVE', '病假'],
    ['PERSONAL_LEAVE', '事假'], ['LATE', '迟到'], ['EARLY', '早退'],
].map(choice => Object.freeze(choice)));

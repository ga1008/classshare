export const STUDENT_SECURITY_MODAL_ID = 'student-security-modal';
export const STUDENT_PASSWORD_CHANGE_FORM_ID = 'student-password-change-form';
export const STUDENT_SECURITY_TRIGGER_SELECTOR = '[data-open-student-security]';
export const STUDENT_PASSWORD_CHANGE_URL = '/api/student/password/change';

export const STUDENT_SECURITY_PENDING_TEXT = '保存中...';
export const STUDENT_SECURITY_SUCCESS_FALLBACK = '密码修改成功。';
export const STUDENT_SECURITY_ERROR_FALLBACK = '密码修改失败。';

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function textFrom(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function passwordChangeSuccessMessage(payload: unknown): string {
  return textFrom(asRecord(payload).message) || STUDENT_SECURITY_SUCCESS_FALLBACK;
}

export function passwordChangeErrorMessage(payload: unknown): string {
  const record = asRecord(payload);
  return (
    textFrom(record.detail)
    || textFrom(record.message)
    || textFrom(record.error)
    || (typeof payload === 'string' ? payload.trim() : '')
    || STUDENT_SECURITY_ERROR_FALLBACK
  );
}

import { describe, expect, it } from 'vitest';

import {
  passwordChangeErrorMessage,
  passwordChangeSuccessMessage,
  STUDENT_SECURITY_ERROR_FALLBACK,
  STUDENT_SECURITY_SUCCESS_FALLBACK,
} from '@/lib/student-security';

describe('student-security helpers', () => {
  it('uses backend success messages when present', () => {
    expect(passwordChangeSuccessMessage({ message: '已更新' })).toBe('已更新');
    expect(passwordChangeSuccessMessage({})).toBe(STUDENT_SECURITY_SUCCESS_FALLBACK);
  });

  it('normalizes backend error payloads in priority order', () => {
    expect(passwordChangeErrorMessage({ detail: '当前密码错误' })).toBe('当前密码错误');
    expect(passwordChangeErrorMessage({ message: '密码太短' })).toBe('密码太短');
    expect(passwordChangeErrorMessage({ error: '请求失败' })).toBe('请求失败');
    expect(passwordChangeErrorMessage('服务暂不可用')).toBe('服务暂不可用');
    expect(passwordChangeErrorMessage({})).toBe(STUDENT_SECURITY_ERROR_FALLBACK);
  });
});

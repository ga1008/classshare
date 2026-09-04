import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { readFixture } from '../fixtures/p03';

test('password recovery accepts a class number with inline hints on desktop and mobile', async ({ page }, testInfo) => {
  const fixture = readFixture();
  const runtimeRoot = path.resolve(fixture.runtimeRoot);
  expect(path.resolve(fixture.databasePath).startsWith(runtimeRoot + path.sep)).toBe(true);
  expect(runtimeRoot.startsWith(path.resolve('.codex-temp') + path.sep)).toBe(true);
  const python = ['venv/Scripts/python.exe', '.venv/Scripts/python.exe'].find(fs.existsSync) || 'python';
  execFileSync(python, ['-c', `
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as conn:
    conn.execute("UPDATE classes SET name = ?, department = ? WHERE id = (SELECT class_id FROM students WHERE id = ?)",
                 (sys.argv[3], sys.argv[4], sys.argv[2]))
    conn.execute("DELETE FROM student_password_reset_requests WHERE student_id = ?", (sys.argv[2],))
`, fixture.databasePath, String(fixture.student.id), '网工2601班（专升本）', '网络工程系']);

  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/student/login');
  await page.locator('#forgot-password-trigger').click();
  await page.locator('#forgot-name').fill(fixture.student.name);
  await page.locator('#forgot-student-id').fill(fixture.student.studentNumber);
  await expect(page.locator('#forgot-class-prefix')).toHaveText('网工');
  await expect(page.locator('#forgot-class-suffix')).toHaveText('班（专升本）');
  await expect(page.locator('#forgot-class-name')).toHaveAttribute('placeholder', '数字或系别即可');

  // Changing identity clears an earlier student's hint immediately.
  await page.locator('#forgot-name').fill('Unknown Student');
  await expect(page.locator('#forgot-class-prefix')).toBeEmpty();
  await page.locator('#forgot-name').fill(fixture.student.name);
  await expect(page.locator('#forgot-class-prefix')).toHaveText('网工');
  await page.locator('#forgot-class-name').fill('2601');
  for (const [label, width, height] of [['desktop', 1440, 980], ['mobile', 375, 812], ['small-mobile', 320, 740]] as const) {
    await page.setViewportSize({ width, height });
    const input = await page.locator('#forgot-class-name').boundingBox();
    const suffix = await page.locator('#forgot-class-suffix').boundingBox();
    expect(input!.width).toBeGreaterThan(100);
    expect(input!.x + input!.width).toBeLessThanOrEqual(suffix!.x);
    expect(suffix!.x + suffix!.width).toBeLessThanOrEqual(width);
    await page.screenshot({ path: testInfo.outputPath(`password-recovery-${label}.png`) });
  }
  const submitted = page.waitForResponse((response) => response.url().endsWith('/api/student/password/forgot'));
  await page.locator('#student-forgot-password-form button[type=submit]').click();
  const response = await submitted;
  expect(response.status()).toBe(200);
  expect((await response.json()).message).toContain('等待教师审核');
  await expect(page.locator('#forgot-password-modal')).not.toBeVisible();
  await page.locator('#forgot-password-trigger').click();
  await expect(page.locator('#forgot-class-prefix')).toBeEmpty();
  await expect(page.locator('#forgot-class-suffix')).toBeEmpty();
  await expect(page.locator('#forgot-class-name')).toHaveValue('');
  expect(errors).toEqual([]);
});

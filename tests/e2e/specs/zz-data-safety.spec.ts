import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { readFixture } from '../fixtures/p03';

test.describe('P03 runtime data safety', () => {
  test('copied runtime database remains isolated and passes quick_check', async () => {
    const fixture = readFixture();
    const payload = JSON.parse(
      execFileSync(
        'python',
        [
          path.join(process.cwd(), 'tests', 'e2e', 'scripts', 'check_p03_runtime.py'),
          '--runtime-root',
          fixture.runtimeRoot,
          '--json',
        ],
        { encoding: 'utf8' },
      ),
    );
    expect(payload.status).toBe('success');
    expect(payload.quickCheck).toBe('ok');
    expect(String(payload.databasePath).replaceAll('\\', '/')).toContain('/.codex-temp/p03-runtime/db/classroom.db');
    expect(String(payload.databasePath)).toBe(fixture.databasePath);
  });
});

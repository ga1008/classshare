import { test as base, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { readFixture, type P03Fixture } from './p03';

export type S3Fixture = P03Fixture & {
  uiV3Synthetic: true;
  lqS3Synthetic: true;
  s3: {
    authoringPaperId: string;
    examTakeAssignmentId: number; examFailureAssignmentId: number; examDraftAssignmentId: number; examDeadlineAssignmentId: number;
    draftAssignmentId: number; concurrencyAssignmentId: number; concurrencySubmissionId: number;
    returnAssignmentId: number; returnSubmissionId: number;
    wrongAssignmentId: number; wrongSubmissionId: number; textbookId: number;
    semesterId: number; manageOfferingId: number;
  };
  reportCard: {
    studentId: number; offeringId: number; publicationId: number;
    assignmentIds: Record<string, number>; expectedMine: (number | null)[];
  };
};

export function readS3Fixture(): S3Fixture {
  if (!process.env.P03_RUNTIME_ROOT) throw new Error('S3 requires an explicit synthetic runtime');
  const fixture = readFixture() as S3Fixture;
  const root = path.resolve(fixture.runtimeRoot);
  const relative = path.relative(path.resolve('.codex-temp'), root);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)
      || fixture.uiV3Synthetic !== true || fixture.lqS3Synthetic !== true
      || path.resolve(fixture.databasePath) !== path.join(root, 'db', 'classroom.db')) {
    throw new Error('S3 fixture identity is not an owned synthetic child');
  }
  return fixture;
}

export async function guardS3Page(page: Page): Promise<S3Fixture> {
  const fixture = readS3Fixture();
  const response = await page.request.get('/api/internal/health');
  expect(response.status()).toBe(200);
  expect((await response.json()).database_path).toBe(fixture.databasePath);
  return fixture;
}

// No application imports, dotenv, or write-capable SQLite connection. Queries
// are authored by the tests and observe only the exact asserted fixture DB.
export function readS3Rows<T = Record<string, unknown>>(sql: string, parameters: unknown[] = []): T[] {
  const fixture = readS3Fixture();
  if (!/^\s*SELECT\b/i.test(sql)) throw new Error('S3 observation requires SELECT');
  const python = path.resolve(process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
  if (!fs.existsSync(python)) throw new Error('Use the project Python runtime');
  const code = `import json, sqlite3, sys\nfrom pathlib import Path\nwith sqlite3.connect(Path(sys.argv[1]).as_uri()+'?mode=ro', uri=True) as conn:\n conn.row_factory=sqlite3.Row\n print(json.dumps([dict(row) for row in conn.execute(sys.argv[2],json.loads(sys.argv[3]))],ensure_ascii=True))`;
  return JSON.parse(execFileSync(python, ['-c', code, fixture.databasePath, sql, JSON.stringify(parameters)], { encoding: 'utf8' }));
}

export const test = base.extend<{ _s3Identity: void }>({
  _s3Identity: [async ({ context, baseURL }, use) => {
    const fixture = readS3Fixture();
    const url = new URL(baseURL || 'about:blank');
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)) {
      throw new Error('S3 browser may use only an explicitly started loopback runtime');
    }
    const response = await context.request.get('/api/internal/health');
    expect(response.status()).toBe(200);
    expect((await response.json()).database_path).toBe(fixture.databasePath);
    await context.route('**/*', route => {
      const target = new URL(route.request().url());
      return ['127.0.0.1', 'localhost'].includes(target.hostname) || ['data:', 'blob:'].includes(target.protocol)
        ? route.continue() : route.abort();
    });
    await use();
  }, { auto: true }],
});
export { expect };

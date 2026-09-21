import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { readFixture, loginTeacher } from '../fixtures/p03';
import { readS3Rows, settleEntranceAnimations } from '../fixtures/lq-s3';

// S6 A package (attendance report runtime tables, manage-pages family).
//
// Runtime setup, in order:
//   python tests/e2e/scripts/prepare_ui_v3_runtime.py --runtime-root .codex-temp/claude-s6-a-runtime
//   python tools/ui/prepare_lq_s3.py .codex-temp/claude-s6-a-runtime
//   python .codex-temp/claude-s6-a-seed-attendance.py .codex-temp/claude-s6-a-runtime
// The shared synthetic runtime has no attendance archive, so without the seeder
// every table below would be an empty state. These specs therefore assert the
// exact seeded row count first and fail hard when it is missing.
//
// LQ_S6_PORT runs LANSHARE_LQ_FAMILIES=manage-shell,navbar-shell,manage-pages
// with LANSHARE_LQ_PILOT=true; LQ_S6_PORT_OFF runs neither (runbook §10, 8215/8216).

const graph = process.env.LQ_S6_GRAPH || JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;
const OFF_ORIGIN = `http://127.0.0.1:${process.env.LQ_S6_PORT_OFF || '8216'}`;
const LIST = '/manage/archive/attendance-reports';

type SeedReport = { id: number; course_name: string; course_code: string; class_name: string; source_state: string; parse_state: string | null };
type SeedCell = { row_index: number; source_name: string; student_number: string; source_class_name: string; normalized_status: string; quality_state: string };

const KNOWN = ['CHECKED', 'UNCHECKED', 'SICK_LEAVE', 'PERSONAL_LEAVE', 'LATE_OR_EARLY'];
const VERIFIED = ['verified', 'resolved_historical_difference'];

/** Ground truth straight from the runtime DB, in the page's own display order. */
function seededReports(): SeedReport[] {
  const rows = readS3Rows<SeedReport>(`SELECT r.id, b.remote_course_name AS course_name, b.remote_course_id AS course_code,
      b.remote_class_name AS class_name, v.source_state, p.state AS parse_state
    FROM attendance_reports r
    JOIN smart_attendance_source_bindings b ON b.id = r.binding_id
    LEFT JOIN attendance_report_versions v ON v.id = (SELECT MAX(v1.id) FROM attendance_report_versions v1 WHERE v1.report_id = r.id)
    LEFT JOIN attendance_parse_runs p ON p.source_version_id = v.id
    WHERE r.deleted_at IS NULL ORDER BY r.updated_at DESC, r.id DESC`);
  expect(rows.length, 'run .codex-temp/claude-s6-a-seed-attendance.py against the S6 A runtime first').toBe(3);
  return rows;
}

/** Mirror of services/attendance_fact_service.summarize_attendance for one student. */
function seededStudents() {
  const cells = readS3Rows<SeedCell>(`SELECT s.row_index, s.source_name, s.student_number, s.source_class_name,
      c.normalized_status, c.quality_state
    FROM attendance_report_students s
    JOIN attendance_report_cells c ON c.student_row_id = s.id AND c.parse_run_id = s.parse_run_id
    ORDER BY s.row_index, c.session_column_id`);
  expect(cells.length, 'the seeded parse run must carry 3 students x 4 sessions').toBe(12);
  const byRow = new Map<number, SeedCell[]>();
  for (const cell of cells) byRow.set(cell.row_index, [...(byRow.get(cell.row_index) || []), cell]);
  return [...byRow.entries()].sort((a, b) => a[0] - b[0]).map(([, rows]) => {
    const counts: Record<string, number> = { CHECKED: 0, UNCHECKED: 0, SICK_LEAVE: 0, PERSONAL_LEAVE: 0, LATE_OR_EARLY: 0, NOT_APPLICABLE: 0, UNKNOWN: 0 };
    for (const cell of rows) {
      const usable = VERIFIED.includes(cell.quality_state) && [...KNOWN, 'NOT_APPLICABLE'].includes(cell.normalized_status);
      counts[usable ? cell.normalized_status : 'UNKNOWN'] += 1;
    }
    const known = KNOWN.reduce((total, status) => total + counts[status], 0);
    const applicable = known + counts.UNKNOWN;
    const rate = (value: number | null) => (applicable === 0 ? '暂无适用点名' : value === null ? '待核实' : `${value.toFixed(1)}%`);
    return {
      name: rows[0].source_name, number: rows[0].student_number, className: rows[0].source_class_name, counts,
      completeness: rate(applicable ? (100 * known) / applicable : null),
      knownRate: rate(known ? (100 * counts.CHECKED) / known : null),
      fullRate: rate(applicable && !counts.UNKNOWN ? (100 * counts.CHECKED) / applicable : null),
    };
  });
}

// Mirrors the `labels` map in static/js/attendance_reports.js.
const CELL_LABELS: Record<string, string> = {
  CHECKED: '出勤', UNCHECKED: '缺课', SICK_LEAVE: '病假', PERSONAL_LEAVE: '事假',
  LATE_OR_EARLY: '迟到或早退', UNKNOWN: '待核实', NOT_APPLICABLE: '不适用',
};

function seededSessions() {
  const rows = readS3Rows<{ id: number; column_index: number; source_header: string; mapping_state: string }>(
    'SELECT id, column_index, source_header, mapping_state FROM attendance_report_sessions ORDER BY column_index');
  expect(rows.length, 'the seeded parse run must carry 4 session columns').toBe(4);
  return rows;
}

/** Cells in the matrix's own DOM order: by student row, then by session column. */
function seededCells() {
  const rows = readS3Rows<{ id: number; normalized_status: string; quality_state: string }>(
    `SELECT c.id, c.normalized_status, c.quality_state FROM attendance_report_cells c
     JOIN attendance_report_students s ON s.id = c.student_row_id AND s.parse_run_id = c.parse_run_id
     ORDER BY s.row_index, c.session_column_id`);
  expect(rows.length, 'the seeded parse run must carry 12 cells').toBe(12);
  return rows;
}

/** Reads an LQ table as {column label -> rendered value} rows. */
async function readLqTable(page: Page, id: string) {
  return page.evaluate((tableId) => {
    const table = document.getElementById(tableId);
    if (!table) return null;
    return [...table.querySelectorAll('tbody tr')].map((row) => Object.fromEntries(
      [...row.children].map((cell) => [cell.getAttribute('data-label') || '', (cell.querySelector('.lq-table__cell')?.textContent || '').trim()]),
    ));
  }, id);
}

async function loginTeacherAt(page: Page, fixture: ReturnType<typeof readFixture>, origin: string) {
  await page.goto(`${origin}/teacher/login`);
  await expect(page.locator('#email')).toBeVisible();
  await page.locator('#email').fill(fixture.teacher.email);
  await page.locator('#password').fill(fixture.password);
  await Promise.all([
    page.waitForURL(/\/dashboard(?:\?|$)/, { timeout: 20_000 }),
    page.locator('button[type="submit"]').click(),
  ]);
}

test(`S6 A archive list renders an LQ record table from the seeded reports (graph ${graph.slice(0, 8)})`, async ({ page }) => {
  const reports = seededReports();
  await loginTeacher(page, readFixture());
  await page.goto(LIST);
  await expect(page.locator('#attReports--lq-wrap[data-lq-table]')).toHaveCount(1);
  // The legacy string table must be gone on this branch, not merely hidden.
  await expect(page.locator('.att-list-table')).toHaveCount(0);
  await expect(page.locator('#attReports tbody tr')).toHaveCount(reports.length);

  const rendered = await readLqTable(page, 'attReports');
  expect(rendered).not.toBeNull();
  expect(rendered!.map((row) => row['课程'])).toEqual(reports.map((report) => report.course_name));
  expect(rendered!.map((row) => row['教学班'])).toEqual(reports.map((report) => `${report.class_name} · ${report.course_code}`));
  expect(rendered!.map((row) => row['学年学期'])).toEqual(reports.map(() => '2025-2026 第2学期'));
  // Only the first report has a parse run: 3 students x 4 sessions.
  expect(rendered!.map((row) => row['学生 × 点名'])).toEqual(['3 × 4', '0 × 0', '0 × 0']);
  expect(rendered!.map((row) => row['状态'])).toEqual(['待核对', '原件已缓存', '失败']);

  // The status badge is a real LQ status chip carrying the mapped tone.
  expect(await page.$$eval('#attReports tbody .lq-chip', (chips) => chips.map((chip) => chip.getAttribute('data-tone'))))
    .toEqual(['warning', 'neutral', 'danger']);

  // The course title is still a link to the detail route, and the row actions
  // still carry every hook ArchiveController.action() dispatches on.
  const hooks = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#attReports tbody tr')];
    return rows.map((row) => ({
      title: row.querySelector('th a')?.getAttribute('href') || null,
      actions: [...row.querySelectorAll('.att-row-actions > *')].map((node) => ({
        text: (node.textContent || '').trim(), href: node.getAttribute('href'), action: node.getAttribute('data-att-action'),
      })),
    }));
  });
  expect(hooks[0].title).toBe(`/manage/archive/attendance-reports/${reports[0].id}`);
  expect(hooks[0].actions.map((action) => action.text)).toEqual(['查看', '原件']);
  expect(hooks[0].actions[1].href).toContain('/source.pdf?download=1');
  // The failed export has no cached original, so it offers no download.
  expect(hooks[2].actions.map((action) => action.text)).toEqual(['查看']);

  // Navigating through the migrated title still reaches the detail view.
  await page.locator('#attReports tbody th a').first().click();
  await expect(page.locator('[data-att-detail-view]')).toBeVisible();
});

test('S6 A student summary renders an LQ matrix table whose numbers match the parse run', async ({ page }) => {
  const reports = seededReports();
  const students = seededStudents();
  await loginTeacher(page, readFixture());
  await page.goto(`${LIST}/${reports[0].id}`);
  await page.locator('[data-att-tab="students"]').click();
  await expect(page.locator('#attStudentSummary--lq-wrap[data-lq-table]')).toHaveCount(1);
  await expect(page.locator('#attStudentSummary tbody tr')).toHaveCount(students.length);

  const rendered = await readLqTable(page, 'attStudentSummary');
  expect(rendered!.map((row) => row['姓名'])).toEqual(students.map((student) => student.name));
  expect(rendered!.map((row) => row['学号'])).toEqual(students.map((student) => student.number));
  expect(rendered!.map((row) => row['班级'])).toEqual(students.map((student) => student.className));
  for (const [column, status] of [['出勤', 'CHECKED'], ['缺课', 'UNCHECKED'], ['病假', 'SICK_LEAVE'],
    ['事假', 'PERSONAL_LEAVE'], ['迟到或早退', 'LATE_OR_EARLY'], ['不适用', 'NOT_APPLICABLE'], ['待核实', 'UNKNOWN']] as const) {
    expect(rendered!.map((row) => row[column]), column).toEqual(students.map((student) => String(student.counts[status])));
  }
  expect(rendered!.map((row) => row['完整率'])).toEqual(students.map((student) => student.completeness));
  expect(rendered!.map((row) => row['已知记录出勤率'])).toEqual(students.map((student) => student.knownRate));
  expect(rendered!.map((row) => row['完整来源出勤率'])).toEqual(students.map((student) => student.fullRate));
  // The three percent() branches are all exercised by the seed, so a regression
  // that collapses them into one string cannot pass.
  expect(rendered!.map((row) => row['完整来源出勤率'])).toContain('暂无适用点名');
  expect(rendered!.map((row) => row['完整来源出勤率'])).toContain('待核实');

  // Every row still owns its review trigger with the student row id.
  const reviews = await page.$$eval('#attStudentSummary tbody [data-att-action="review-student"]',
    (nodes) => nodes.map((node) => ({ text: (node.textContent || '').trim(), id: node.getAttribute('data-id') })));
  expect(reviews).toHaveLength(students.length);
  expect(reviews.every((review) => Boolean(review.id))).toBe(true);
  expect([...new Set(reviews.map((review) => review.text))]).toEqual(['关联学生']);
});

test('S6 A matrix renders an LQ table whose column headers keep the review-session control', async ({ page }) => {
  const reports = seededReports();
  const sessions = seededSessions();
  const cells = seededCells();
  await loginTeacher(page, readFixture());
  await page.goto(`${LIST}/${reports[0].id}`);
  await page.locator('[data-att-tab="matrix"]').click();
  await expect(page.locator('#attMatrix--lq-wrap[data-lq-table]')).toHaveCount(1);
  await expect(page.locator('.att-matrix')).toHaveCount(0);

  // Three identity columns plus one column per seeded session.
  const headers = await page.$$eval('#attMatrix thead th', (nodes) => nodes.map((node) => ({
    label: (node.querySelector('.lq-table__colhead')?.firstChild?.textContent || node.textContent || '').trim(),
    slot: node.querySelector('.lq-table__colhead')?.getAttribute('data-lq-slot') || null,
    state: node.querySelector('.lq-table__colhead small')?.textContent?.trim() || null,
    review: node.querySelector('[data-att-action="review-session"]')?.getAttribute('data-id') || null,
  })));
  expect(headers).toHaveLength(3 + sessions.length);
  expect(headers.slice(0, 3).map((header) => header.label)).toEqual(['学生', '学号', '核对']);
  // The identity columns did not opt into the header slot, so their <th> keeps
  // the exact markup it had before the capability was added.
  expect(headers.slice(0, 3).map((header) => header.slot)).toEqual([null, null, null]);
  expect(headers.slice(3).map((header) => header.label)).toEqual(sessions.map((session) => session.source_header));
  expect(headers.slice(3).map((header) => header.slot)).toEqual(sessions.map((_, index) => `col:session-${index}`));
  // openIdentity('session') keeps its only entry point, one per session column.
  expect(headers.slice(3).map((header) => header.review)).toEqual(sessions.map((session) => String(session.id)));
  expect(headers.slice(3).map((header) => header.state)).toEqual(sessions.map(() => '未关联课次'));

  // Every seeded cell is still an att-cell button carrying its own cell id, so
  // openEvidence()'s id lookup into this.cells is untouched.
  const rendered = await page.$$eval('#attMatrix tbody [data-att-action="evidence"]', (nodes) => nodes.map((node) => ({
    id: node.getAttribute('data-id'), text: (node.textContent || '').trim(), className: node.className, title: node.getAttribute('title'),
  })));
  expect(rendered.map((cell) => cell.id)).toEqual(cells.map((cell) => String(cell.id)));
  expect(rendered.map((cell) => cell.text)).toEqual(cells.map((cell) => CELL_LABELS[cell.normalized_status] || '待核实'));
  expect(rendered.map((cell) => cell.className)).toEqual(cells.map((cell) => `att-cell att-status-${cell.normalized_status}`));
  expect(rendered[0].title).toContain(' · ');
  // The legend uses the same att-status-* palette, so it must still line up.
  expect(await page.locator('[data-att-legend] .att-cell').count()).toBe(Object.keys(CELL_LABELS).length);

  // Clicking a migrated cell still opens the evidence dialog.
  await page.locator('#attMatrix tbody [data-att-action="evidence"]').first().click();
  await expect.poll(async () => page.$eval('[data-att-evidence-dialog]', (node) => (node as HTMLDialogElement).open)).toBe(true);
  await page.locator('[data-att-action="close-evidence"]').click();
  await expect.poll(async () => page.$eval('[data-att-evidence-dialog]', (node) => (node as HTMLDialogElement).open)).toBe(false);
});

// createTable validates that slot Nodes do not overlap, which is O(n^2) over
// every slot node. The matrix is the first consumer that can reach 25 rows x 20
// session columns, so measure the real worst case instead of assuming it is fine.
test('S6 A the factory builds a worst-case 25x20 matrix within a sane budget', async ({ page }, info) => {
  await loginTeacher(page, readFixture());
  await page.goto(LIST);
  const millis = await page.evaluate(async () => {
    const { createTable } = await import('/static/js/lq/tables.js');
    const columns = [{ key: 'name', label: '学生', rowHeader: true }];
    const slots: Record<string, Node[]> = {};
    for (let column = 0; column < 20; column += 1) {
      columns.push({ key: `s${column}`, label: `第 ${column} 次`, slot: true } as never);
      slots[`col:s${column}`] = [document.createElement('button')];
    }
    const rows = Array.from({ length: 25 }, (_, row) => {
      const cells: Record<string, string> = { name: `学生${row}` };
      for (let column = 0; column < 20; column += 1) {
        cells[`s${column}`] = '';
        slots[`cell:r${row}:s${column}`] = [document.createElement('button')];
      }
      return { key: `r${row}`, cells };
    });
    const start = performance.now();
    const node = createTable('table', { id: 'bench', caption: '基准', mode: 'matrix', columns, rows }, slots);
    const elapsed = performance.now() - start;
    if (node.querySelectorAll('tbody tr').length !== 25) throw new Error('benchmark table did not build');
    return elapsed;
  });
  // Recorded in the report; the assertion only guards against a pathological
  // regression, not against normal machine-to-machine variance.
  info.annotations.push({ type: 'timing', description: `createTable 25x20 (525 slot nodes) took ${millis.toFixed(1)}ms` });
  expect(millis, `createTable 25x20 took ${millis.toFixed(1)}ms`).toBeLessThan(2000);
});

test('S6 A manage-pages family closed keeps the legacy tables unchanged', async ({ page }) => {
  const reports = seededReports();
  await loginTeacherAt(page, readFixture(), OFF_ORIGIN);
  const list = await page.goto(`${OFF_ORIGIN}${LIST}`);
  expect(list?.status()).toBe(200);
  await expect(page.locator('.att-list-table')).toHaveCount(1);
  expect(await page.locator('[data-attendance-root]').getAttribute('data-lq-tables')).toBeNull();
  await expect(page.locator('[data-lq-table]')).toHaveCount(0);
  await expect(page.locator('.att-list-table tbody tr')).toHaveCount(reports.length);
  // The legacy list keeps its single merged course cell and its data-label hooks.
  const legacy = await page.evaluate(() => {
    const row = document.querySelector('.att-list-table tbody tr')!;
    return {
      headers: [...document.querySelectorAll('.att-list-table thead th')].map((cell) => (cell.textContent || '').trim()),
      title: row.querySelector('td strong')?.textContent?.trim() || null,
      subtitle: row.querySelector('td small')?.textContent?.trim() || null,
      labels: [...row.querySelectorAll('td[data-label]')].map((cell) => cell.getAttribute('data-label')),
      badge: row.querySelector('.att-badge')?.className || null,
    };
  });
  expect(legacy.headers).toEqual(['课程 / 教学班', '学年学期', '学生 × 点名', '状态', '更新时间', '操作']);
  expect(legacy.title).toBe(reports[0].course_name);
  expect(legacy.subtitle).toBe(`${reports[0].class_name} · ${reports[0].course_code}`);
  expect(legacy.labels).toEqual(['学期', '规模', '更新']);
  expect(legacy.badge).toBe('att-badge att-badge--warn');

  await page.goto(`${OFF_ORIGIN}${LIST}/${reports[0].id}`);
  await page.locator('[data-att-tab="students"]').click();
  await expect(page.locator('[data-att-students-content] table.att-table')).toHaveCount(1);
  await expect(page.locator('[data-att-students-content] [data-lq-table]')).toHaveCount(0);
  expect(await page.$$eval('[data-att-students-content] thead th', (cells) => cells.map((cell) => (cell.textContent || '').trim())))
    .toEqual(['姓名 / 学号', '班级', '出勤', '缺课', '病假', '事假', '迟到或早退', '不适用', '待核实', '完整率', '已知记录出勤率', '完整来源出勤率']);
  await expect(page.locator('[data-att-students-content] tbody tr')).toHaveCount(3);

  // The matrix keeps the legacy .att-matrix scroll wrapper and its merged first
  // column, with the review-session control inline in each column header.
  await page.locator('[data-att-tab="matrix"]').click();
  await expect(page.locator('.att-matrix table.att-table')).toHaveCount(1);
  await expect(page.locator('[data-att-matrix-content] [data-lq-table]')).toHaveCount(0);
  await expect(page.locator('.att-matrix thead [data-att-action="review-session"]')).toHaveCount(4);
  await expect(page.locator('.att-matrix thead small')).toHaveCount(4);
  await expect(page.locator('.att-matrix tbody [data-att-action="evidence"]')).toHaveCount(12);
});

test('S6 A migrated tables pass axe on both switch branches', async ({ page }) => {
  const reports = seededReports();
  const fixture = readFixture();
  for (const origin of ['', OFF_ORIGIN]) {
    if (origin) await loginTeacherAt(page, fixture, origin);
    else await loginTeacher(page, fixture);
    for (const tab of ['', 'students', 'matrix'] as const) {
      const route = tab ? `${LIST}/${reports[0].id}` : LIST;
      await page.goto(`${origin}${route}`);
      if (tab) await page.locator(`[data-att-tab="${tab}"]`).click();
      const host = tab ? `[data-att-${tab === 'students' ? 'students-content' : 'matrix-content'}]` : '[data-att-reports]';
      await expect(page.locator(`${host} table`)).toHaveCount(1);
      await settleEntranceAnimations(page);
      const scan = await new AxeBuilder({ page }).include('[data-attendance-root]').analyze();
      expect(scan.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical'),
        `${origin || 'family-on'} ${route} ${tab || 'list'}`).toEqual([]);
    }
  }
});

// The filter-summary container used to carry aria-label with no role, which ARIA
// forbids. It was given role=group on 2026-09-22, on the shared template line, so
// both branches are clean now. Keep probing it so a regression fails loudly.
test('S6 A the filter summary container carries a valid accessible name', async ({ page }) => {
  const fixture = readFixture();
  const probe = async (origin: string) => {
    if (origin) await loginTeacherAt(page, fixture, origin);
    else await loginTeacher(page, fixture);
    await page.goto(`${origin}${LIST}`);
    await expect(page.locator('[data-att-reports] table')).toHaveCount(1);
    await settleEntranceAnimations(page);
    const scan = await new AxeBuilder({ page }).include('.att-chips').analyze();
    return scan.violations.map((violation) => violation.id).sort();
  };
  const on = await probe('');
  const off = await probe(OFF_ORIGIN);
  expect(on).toEqual([]);
  expect(off).toEqual(on);
});

for (const scheme of ['light', 'dark'] as const) {
  for (const width of [1440, 390] as const) {
    test(`S6 A attendance tables at ${width} ${scheme}`, async ({ page }, info) => {
      const reports = seededReports();
      const fixture = readFixture();
      await page.emulateMedia({ colorScheme: scheme });
      await page.setViewportSize({ width, height: 1000 });
      for (const [branch, origin] of [['on', ''], ['off', OFF_ORIGIN]] as const) {
        if (origin) await loginTeacherAt(page, fixture, origin);
        else await loginTeacher(page, fixture);
        await page.goto(`${origin}${LIST}`);
        await expect(page.locator('[data-att-reports] table')).toHaveCount(1);
        await settleEntranceAnimations(page);
        expect(await page.evaluate(() => document.documentElement.scrollWidth), `${branch} ${width}`).toBeLessThanOrEqual(width);
        await page.screenshot({ path: info.outputPath(`attendance-list-${branch}-${width}-${scheme}.png`), fullPage: true });
        await page.goto(`${origin}${LIST}/${reports[0].id}`);
        for (const tab of ['students', 'matrix'] as const) {
          await page.locator(`[data-att-tab="${tab}"]`).click();
          await expect(page.locator(`[data-att-${tab === 'students' ? 'students-content' : 'matrix-content'}] table`)).toHaveCount(1);
          await settleEntranceAnimations(page);
          await page.screenshot({ path: info.outputPath(`attendance-${tab}-${branch}-${width}-${scheme}.png`), fullPage: true });
        }
      }
    });
  }
}

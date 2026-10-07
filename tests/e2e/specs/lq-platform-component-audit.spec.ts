import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';
import { collectComponentDom } from '../fixtures/lq-component-audit';

type Role = 'anonymous' | 'student' | 'teacher' | 'superTeacher';
type Route = { method: string; path: string; templates: string[]; roles: Role[]; scope: string; execution: string };
const manifest = JSON.parse(fs.readFileSync(process.env.LQ_PLATFORM_AUDIT_ROUTES || 'docs/lq-platform-browser-routes-2026-09-27.json', 'utf8'));
const sourceAudit = JSON.parse(fs.readFileSync(process.env.LQ_PLATFORM_AUDIT_SOURCES || 'docs/lq-platform-component-audit-2026-09-27-remaining.json', 'utf8'));
const supplemental = JSON.parse(fs.readFileSync('docs/lq-platform-supplemental-contracts-2026-09-27.json', 'utf8'));
function authContract() {
  const contract = supplemental.suites.find((suite: any) => suite.name === 'authentication');
  if (!contract || contract.passed !== 45 || !fs.existsSync(contract.log) || crypto.createHash('sha256').update(fs.readFileSync(contract.log)).digest('hex') !== contract.logSha256) return null;
  if (Object.entries(contract.sourceHashes).some(([file, hash]) => !fs.existsSync(file) || crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') !== hash)) return null;
  return { name: contract.name, command: contract.command, log: contract.log, logSha256: contract.logSha256, passed: contract.passed, scope: 'SSR and in-memory business contract, no generic browser POST' };
}
const auditSources = [...new Set<string>([...sourceAudit.files.map(({ file }: { file: string }) => file), 'static/assets/manifest.json', 'static/css/tailwind-app.css', 'static/css/ui-system.src.css', ...fs.readdirSync('static/css/lq', { recursive: true }).filter(file => String(file).endsWith('.css')).map(file => path.join('static/css/lq', String(file)))])];
const sourceHashes = () => Object.fromEntries(auditSources.map(file => [file, fs.existsSync(file) ? crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') : null]));
const output = path.resolve(process.env.LQ_PLATFORM_AUDIT_OUTPUT || '.codex-temp/lq-platform-browser-audit');
const widths = (process.env.LQ_PLATFORM_AUDIT_WIDTHS || '390,1440').split(',').map(Number);
const roles = (process.env.LQ_PLATFORM_AUDIT_ROLES || 'anonymous,student,teacher,superTeacher').split(',') as Role[];
const appearances = (process.env.LQ_PLATFORM_AUDIT_APPEARANCES || 'light,dark').split(',');
const routeFilter = process.env.LQ_PLATFORM_AUDIT_ROUTE ? new RegExp(process.env.LQ_PLATFORM_AUDIT_ROUTE) : null;

function resolveRoutes(route: Route, fixture: any, role: Role): { urls: string[]; gap?: string } {
  const captures = [...(fixture.lqCaptureRoutes?.teacher || []), ...(fixture.lqCaptureRoutes?.student || [])];
  const fromCapture = (id: string) => captures.find(([name]: [string, string]) => name === id)?.[1];
  const special: Record<string, string | undefined> = {
    '/lesson-plan/{plan_id}/edit': fromCapture('lesson-plan-editor'),
    '/assessment-plan/{plan_id}/edit': fromCapture('assessment-plan-editor'),
    '/teacher-evaluation/{evaluation_id}/edit': fromCapture('evaluation-editor'),
    '/exam/take/{assignment_id}': fromCapture('exam-take') || (fixture.s3?.examTakeAssignmentId ? `/exam/take/${fixture.s3.examTakeAssignmentId}` : undefined),
  };
  if (Object.hasOwn(special, route.path)) return special[route.path] ? { urls: [special[route.path]!] } : { urls: [], gap: 'Run prepare_lq_pages.py on this owned synthetic fixture; no fabricated IDs.' };
  if (route.path === '/auth/forbidden') return { urls: [`/auth/forbidden?required_role=${role === 'student' ? 'teacher' : 'student'}&next=${role === 'student' ? '/manage' : '/resume'}`] };
  if (route.path === '/profile') return { urls: ['overview', 'settings', 'appearance', 'security', 'notifications', 'private', 'email'].map(section => `/profile?section=${section}`) };
  if (route.path === '/resume/profile/{section}') return { urls: ['education', 'experience', 'skill', 'certificate', 'self-intro'].map(section => `/resume/profile/${section}`) };
  if (route.path.includes('{report_id}') && fixture.platformAudit?.attendanceReportIds) return { urls: fixture.platformAudit.attendanceReportIds.map((id: number) => route.path.replace('{report_id}', String(id))) };
  if (route.path === '/materials/render-view/{material_id}' && fixture.platformAudit?.htmlMaterialId) return { urls: [route.path.replace('{material_id}', String(fixture.platformAudit.htmlMaterialId))] };
  if (route.path.includes('{panel_key}')) return { urls: ['overview', 'alerts', 'exams', 'settings'].map(key => route.path.replace('{class_offering_id}', String(fixture.classOfferingId)).replace('{panel_key}', key)) };
  const values: Record<string, unknown> = {
    class_offering_id: fixture.classOfferingId, student_id: fixture.student.id,
    assignment_id: role === 'student' ? fixture.studentSubmissionAssignmentId : fixture.teacherReviewAssignmentId,
    submission_id: fixture.teacherReviewSubmissionId, exam_id: fixture.s3?.authoringPaperId,
    material_id: fixture.visualMaterialIds?.[0], pack_id: fixture.platformAudit?.lessonDocPackId,
  };
  let gap = '';
  const url = route.path.replace(/\{([^}]+)\}/g, (_whole, key) => {
    if (values[key] === undefined) gap = `Unresolved owned fixture parameter: ${key}`;
    return String(values[key] ?? `{${key}}`);
  });
  return gap ? { urls: [], gap } : { urls: [url] };
}

for (const role of roles) for (const width of widths) for (const appearance of appearances) {
  test(`platform component DOM ${role} ${width} ${appearance}`, async ({ page }, info) => {
    const sourceAtStart = sourceHashes();
    const fixture = readS3Fixture(); // Fixture + loopback identity also asserted by automatic guard.
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: appearance === 'dark' ? 'dark' : 'light' });
    if (role === 'student') await loginStudent(page, fixture);
    if (role === 'teacher' || role === 'superTeacher') await loginTeacher(page, fixture, role === 'superTeacher' ? fixture.superTeacher : fixture.teacher);
    const cases = (manifest.routes as Route[]).filter(route => route.scope !== 'development' && route.roles.includes(role) && (!routeFilter || routeFilter.test(route.path)));
    const results: any[] = [], browserErrors: string[] = [];
    const onError = (error: Error) => browserErrors.push(error.message);
    page.on('pageerror', onError);
    fs.mkdirSync(output, { recursive: true });
    const artifact = path.join(output, `${role}-${width}-${appearance}.json`);
    const persist = () => fs.writeFileSync(artifact, JSON.stringify({ acceptance: 'pending', role, width, appearance,
      sourceAtStart, sourceChangedDuringRun: Object.entries(sourceHashes()).filter(([file, hash]) => sourceAtStart[file] !== hash).map(([file]) => file),
      fixture: { runtimeRoot: fixture.runtimeRoot, databasePath: fixture.databasePath },
      routesSelected: cases.length, excludedByFilter: (manifest.routes as Route[]).filter(route => route.roles.includes(role)).length - cases.length,
      results, browserErrors, exceptionalDocuments: manifest.exceptionalDocuments,
      dynamicDialogCoverage: { status: 'pending-domain-scenarios', suites: manifest.domainScenarioSuites },
      limitation: 'GET baseline including hidden DOM, open shadow roots and frames. Native/dynamic modal workflows require linked domain scenarios; declarations do not prove acceptance.' }, null, 2));
    try {
      for (const route of cases) {
        if (route.method !== 'GET' || route.scope === 'development') {
          const contract = route.method === 'POST' && ['/student/login', '/student/login/identity', '/student/password/forgot', '/student/password/setup', '/teacher/login', '/teacher/register'].includes(route.path) ? authContract() : null;
          results.push({ route, status: contract ? 'scoped-contract-covered-browser-not-run' : 'not-exercised', reason: route.execution, contract }); persist(); continue;
        }
        const resolved = resolveRoutes(route, fixture, role);
        if (resolved.gap) { results.push({ route, status: 'missing-synthetic-scenario', reason: resolved.gap }); persist(); continue; }
        for (const url of resolved.urls) {
          const errorsBefore = browserErrors.length;
          try {
            const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
            await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => undefined);
            await page.evaluate(async selectedAppearance => {
              const runtime = (document as any)[Symbol.for('lanshare.theme.installation')];
              const core = (window as any).LanShareTheme;
              if (runtime && core) runtime.refresh({ ...core.readPreferences(document.documentElement), appearance: selectedAppearance });
              await document.fonts.ready;
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }, appearance);
            const frames = await collectComponentDom(page, sourceAudit.entries);
            const finalUrl = new URL(page.url());
            const requested = new URL(url, page.url());
            const redirected = finalUrl.pathname !== requested.pathname;
            const status = response?.status();
            const declaredAlias = redirected ? (manifest.routes as Route[]).find(candidate => candidate.method === 'GET' && candidate.path === finalUrl.pathname && candidate.templates.some(template => route.templates.includes(template))) : undefined;
            let screenshot: string | null = null;
            if (['/student/login', '/teacher/login', '/dashboard', '/classroom/{class_offering_id}', '/manage/system/users', '/materials/lessondoc-editor/{pack_id}'].includes(route.path)) {
              screenshot = path.join(output, `${role}-${width}-${appearance}-${crypto.createHash('sha256').update(url).digest('hex').slice(0, 10)}.png`);
              await page.screenshot({ path: screenshot });
            }
            results.push({ route, requested: url, finalUrl: page.url(), httpStatus: status,
              status: (status === 200 || route.path === '/teacher/register' && status === 403 && authContract()) && (!redirected || declaredAlias) ? 'dom-measured-pending' : 'route-not-covered',
              redirected, expectedStatusEvidence: route.path === '/teacher/register' ? { status: 403, source: 'classroom_app/routers/ui_parts/auth.py:182', reason: 'Registration is intentionally closed; auth contract asserts the status template and no account creation' } : null, aliasEvidence: declaredAlias ? { canonicalPath: declaredAlias.path, sharedTemplates: declaredAlias.templates.filter(template => route.templates.includes(template)), status: 'source-route-template-alias' } : null, frames, screenshot, browserErrors: browserErrors.slice(errorsBefore) });
          } catch (error) { results.push({ route, requested: url, status: 'unmeasured', error: String(error) }); }
          persist();
        }
      }
      await info.attach('component-dom-audit', { path: artifact, contentType: 'application/json' });
      if (process.env.LQ_PLATFORM_AUDIT_ENFORCE === '1') {
        expect(results.filter(row => !['dom-measured-pending', 'scoped-contract-covered-browser-not-run'].includes(row.status)), 'Every gap must have an executed scenario').toEqual([]);
        expect(results.flatMap(row => row.frames || []).filter(frame => frame.status !== 'measured' || frame.scope !== 'document-content' && (frame.totals?.unowned > 0 || frame.totals?.invalidDeclaration > 0 || frame.totals?.layerContentMissing > 0)), 'Unowned controls remain').toEqual([]);
        // Shared factories intentionally map to multiple call sites. Unknown mappings
        // remain evidence, but do not prove that a canonical control is unowned.
        expect(results.flatMap(row => row.frames || []).filter(frame => frame.overflow > 1), 'Unexpected document horizontal overflow').toEqual([]);
        expect(results.flatMap(row => row.frames || []).filter(frame => frame.scope !== 'document-content' && frame.blurTotals?.leaves > 0), 'Leaf controls must not allocate independent backdrop blur').toEqual([]);
        expect(Object.entries(sourceHashes()).filter(([file, hash]) => sourceAtStart[file] !== hash), 'Source must remain stable during evidence capture').toEqual([]);
        expect(browserErrors).toEqual([]);
      }
    } finally { page.off('pageerror', onError); persist(); }
  });
}

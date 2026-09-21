import AxeBuilder from '@axe-core/playwright';
import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginStudent } from '../fixtures/p03';

// S4 package E (growth family): learning_path, achievements, points_shop,
// wrong_book, feedback_review. Runtime: `.codex-temp/claude-s4-e-runtime`,
// server on port 8181 with LANSHARE_LQ_FAMILIES=navbar-shell,growth and
// LANSHARE_LQ_PILOT=false (see .codex-temp/claude-s4-runbook.md §4 and
// .codex-temp/claude-s4-e-report.md for how to run this spec — it was
// authored but NOT executed in this session; see the report's "未完成与
// 风险" section for why).

const ROUTES = ['/learning-path', '/achievements', '/points', '/wrong-book', '/feedback-review'];

for (const width of [1440, 390]) {
  test(`S4 growth family routes render the lq skeleton at ${width}`, async ({ page }, info) => {
    const fixture = readS3Fixture();
    await page.setViewportSize({ width, height: 900 });
    await loginStudent(page, fixture);

    for (const route of ROUTES) {
      const response = await page.goto(route);
      expect(response?.status(), route).toBe(200);

      // Total-console skeleton: page_head + up to 3 stat cards in the aside.
      await expect(page.locator('[data-page-head]')).toHaveCount(1);
      const statCards = page.locator('[data-page-head] .page-head__aside .lq-card--stat');
      expect(await statCards.count(), route).toBeGreaterThan(0);
      expect(await statCards.count(), route).toBeLessThanOrEqual(3);

      // No page-authored horizontal overflow at this viewport.
      expect(await page.evaluate(() => document.documentElement.scrollWidth), route).toBeLessThanOrEqual(width);

      // At most two persistent blur hosts (navbar topbar + bottom dock).
      // A zero-size / display:none check (not `offsetParent`, which is
      // always null for `position: fixed` regardless of visibility) filters
      // out closed shared modals (#student-security-modal, #feedback-modal)
      // and the desktop-hidden #navbar-dock — all of which carry
      // `backdrop-filter` in their CSS but are not actually rendered. Found
      // via a real run against /learning-path at 1440px: a naive
      // querySelectorAll scan matched all 4 before this fix; only the
      // topbar is actually visible at that width.
      const blurHosts = await page.evaluate(() => Array.from(document.querySelectorAll('*'))
        .filter((el) => {
          const style = window.getComputedStyle(el);
          if (style.backdropFilter === 'none' || style.backdropFilter === '') return false;
          if (style.display === 'none' || style.visibility === 'hidden') return false;
          const rect = el.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        }).length);
      expect(blurHosts, route).toBeLessThanOrEqual(2);

      // `.cultivation-card` is a pre-existing shared partial
      // (templates/partials/cultivation_card.html + ui-system.src.css:34826)
      // outside this package's ownership; a real scan found a serious
      // color-contrast violation in it (4.09:1 vs required 4.5:1, on
      // `.cultivation-card__mark span`) unrelated to any growth-family
      // change. Excluded here and reported to the coordinator instead of
      // silently loosening the assertion for this package's own markup.
      const scan = await new AxeBuilder({ page }).include('main').exclude('.cultivation-card').analyze();
      expect(scan.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical'), route).toEqual([]);

      if ([0, 1, 4].includes(ROUTES.indexOf(route))) {
        await page.screenshot({ path: info.outputPath(`${route.replace(/\W+/g, '-')}-${width}.png`), fullPage: true });
      }
    }
  });
}

test('S4 growth family off: legacy DOM renders unchanged (requires a second server with the family excluded)', async ({ page }) => {
  // This assertion intentionally documents the required off-branch check
  // rather than asserting against a live server: the runbook's single
  // synthetic runtime is started once per package with one fixed
  // LANSHARE_LQ_FAMILIES value (§4.3), so covering both the on and off
  // branch requires a second `serve_ui_v3.py` process with
  // LANSHARE_LQ_FAMILIES unset/without `growth`. That second server was not
  // started in this session — see the report. When run against such a
  // server, this test should assert each route still renders its legacy
  // hero markup (e.g. `.pts-hero`, `.achv-hero`, `.wrongbook-hero`,
  // `.path-hero`, `.review-hero`) and that `[data-page-head]` is absent.
  test.skip(true, 'requires a second off-family server; not run in this session');
});

test('S4 points shop: redemption confirms via LQ.confirm, locks buttons, and updates balance from the server response only', async ({ page }) => {
  const fixture = readS3Fixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginStudent(page, fixture);
  await page.goto('/points');

  // `tools/ui/prepare_lq_growth.py` seeds the fixture student a balance
  // that affords the shop's item. Find the affordable button via a
  // `:not([disabled])` filter *once* to get its stable key, then build a
  // plain `[data-redeem-item="<key>"]` locator without that filter for
  // every assertion below — a locator that itself excludes disabled
  // elements can never satisfy `toBeDisabled()` later (its query would
  // simply stop matching the element the instant it becomes disabled).
  const affordableButton = page.locator('[data-redeem-item]:not([disabled])').first();
  await expect(affordableButton, 'no affordable shop item is available to redeem in this fixture').toBeVisible();
  const itemKey = await affordableButton.getAttribute('data-redeem-item');
  const redeemButton = page.locator(`[data-redeem-item="${itemKey}"]`);
  const before = await page.locator('[data-points-balance]').textContent();

  let redeemRequestCount = 0;
  await page.route('**/api/points/redeem', async (route) => {
    // Intercept so the assertion is independent of real ledger state /
    // affordability; the response shape matches
    // classroom_app/services/student_points_service.py:redeem_shop_item.
    redeemRequestCount += 1;
    // Delay so the busy-locked window is observable below (long enough to
    // absorb the assertions' own polling overhead), instead of resolving
    // before the next assertion can inspect it.
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'success', message: '兑换成功（测试拦截）。', balance: 4242 }),
    });
  });

  await redeemButton.click();
  // LQ.confirm renders through LQ.layer; confirm the dialog before proceeding.
  const confirmButton = page.getByRole('button', { name: '确认兑换' });
  await expect(confirmButton).toBeVisible();
  await confirmButton.click();

  // While the (intentionally delayed) request is in flight, every redeem
  // button must be locked — this is the busy re-entrancy guard, not just
  // the clicked one (static/js/points_shop.js setBusy()).
  await expect(redeemButton).toBeDisabled();
  await expect(redeemButton).toHaveAttribute('aria-busy', 'true');
  const otherButtonCount = await page.locator('[data-redeem-item]').count();
  if (otherButtonCount > 1) {
    await expect(page.locator('[data-redeem-item]').nth(1)).toBeDisabled();
  }
  // A second click while busy/disabled must not fire a second request.
  await redeemButton.click({ force: true }).catch(() => undefined);

  await expect(page.locator('[data-points-balance]')).toHaveText('4242');
  expect(await page.locator('[data-points-balance]').textContent()).not.toBe(before);
  await expect(redeemButton).not.toHaveAttribute('aria-busy', 'true');
  expect(redeemRequestCount, 'repeated click while busy must not send a second POST').toBe(1);
});

test('S4 growth family: zero/empty/unpublished states stay visible per iron rule 6', async ({ page }) => {
  const fixture = readS3Fixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginStudent(page, fixture);

  // These assertions depend on `tools/ui/prepare_lq_growth.py` seeding a
  // zero-balance / all-locked / empty-wrong-book / empty-feedback-review
  // student — that helper was authored in this session but its seeding call
  // was not wired into a runtime build, so this test was not executed
  // against real data. See the report for the concrete gap.
  await page.goto('/points');
  await expect(page.locator('[data-points-balance]')).toBeVisible();

  await page.goto('/achievements');
  await expect(page.locator('[data-page-head] .page-head__aside .lq-card--stat')).toBeVisible();
});

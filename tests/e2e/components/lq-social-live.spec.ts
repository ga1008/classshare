import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Explicit opt-in: real SSR/auth/services, but only this freshly seeded fixture.
const runtime = process.env.SOCIAL_CHAT_RUNTIME ? path.resolve(process.env.SOCIAL_CHAT_RUNTIME) : '';
const fixture = runtime ? JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8')) : null;
const origin = `http://127.0.0.1:${process.env.SOCIAL_CHAT_PORT || '8373'}`;
const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
test.skip(!runtime, 'Requires a task-owned socialChatSeed fixture and loopback server');
if (fixture && (!runtime.startsWith(path.resolve('.codex-temp') + path.sep) || !fixture.uiV3Synthetic
    || !fixture.socialChatSeed || path.resolve(fixture.databasePath) !== path.join(runtime, 'db/classroom.db'))) {
    throw Error('Live social browser writes require the explicit owned synthetic fixture');
}
async function login(page: Page, student = false) {
    const health = await page.request.get(`${origin}/api/internal/health`);
    expect(health.ok()).toBeTruthy();
    expect(path.resolve((await health.json()).database_path)).toBe(path.resolve(fixture.databasePath));
    const response = await page.request.post(`${origin}${student ? '/api/student/login/password' : '/teacher/login'}`, {
        form: student ? { identifier: fixture.student.studentNumber, password: fixture.password }
            : { email: fixture.teacher.email, password: fixture.password, next: '/dashboard' }, maxRedirects: 0,
    });
    expect(response.status()).toBe(student ? 200 : 303);
    if (!student) await page.request.post(`${origin}/api/manage/teacher-onboarding/dismiss`, { data: { reason: 'later' } });
}
async function chat(page: Page) {
    await page.goto(`${origin}/dashboard`);
    await expect(page.locator(`script[src="/static/${graph.entries['js/social/social_client.js']}"]`).first()).toHaveCount(1);
    await page.locator('[data-social-entry]:visible').first().click();
    await expect(page.locator('#social-chat')).toBeVisible();
    await expect(page.locator('#social-friends')).toBeHidden();
}

test('LQ live SSR chat entry, own avatar, private group settings and friend invitation', async ({ page, browser }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await login(page);
    const seed = fixture.socialChatSeed, gid = seed.groupId;
    const current = await (await page.request.get(`${origin}/api/social/groups/${gid}`)).json();
    if (current.members.some((member: any) => member.identity === seed.inviteIdentity))
        expect((await page.request.delete(`${origin}/api/social/groups/${gid}/members/${seed.inviteIdentity}`)).ok()).toBeTruthy();
    await page.request.patch(`${origin}/api/social/groups/${gid}/me`, { data: { alias: '', nickname: '', muted: false } });
    await chat(page);
    await page.locator(`[data-conv="${seed.dmKey}"]`).click();
    await expect(page.locator('.social-msg--out:not(.is-connected) .social-msg__avatar').first()).toBeVisible();
    await expect(page.locator('.social-msg--out:not(.is-connected) .social-msg__avatar > *').first()).toBeVisible();
    await page.screenshot({ path: test.info().outputPath('live-dm-desktop.png') });
    await page.locator('[data-social-manage-friends]').click();
    await expect(page.locator('#social-friends')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#social-friends')).toBeHidden();
    await expect(page.locator('[data-social-manage-friends]')).toBeFocused();
    await page.locator(`[data-conv="group:${gid}"]`).click();
    await expect(page.locator('[data-group-card]')).toHaveCount(3);
    await expect(page.locator('.social-chat__side').getByText('群公告', { exact: true })).toBeHidden();
    await page.locator('[data-group-settings-open]').click();
    await page.locator('#social-group-alias').fill('老师自己的星河');
    await page.locator('#social-group-nickname').fill('林老师');
    await page.locator('[data-group-private-form]').getByRole('button', { name: '保存我的设置', exact: true }).click();
    await expect(page.locator('#social-group-settings')).toContainText('我的设置已保存');
    await page.keyboard.press('Escape');
    await expect(page.locator('#social-group-settings')).toBeHidden();
    await expect(page.locator('.social-chat__title')).toHaveText('老师自己的星河');
    const studentContext = await browser.newContext();
    const student = await studentContext.newPage();
    try {
        await login(student, true);
        const detail = await (await student.request.get(`${origin}/api/social/groups/${gid}`)).json();
        expect(detail.display_name).toBe(detail.name);
        expect(JSON.stringify(detail)).not.toContain('老师自己的星河');
        expect(detail.members.find((member: any) => member.identity === seed.teacherIdentity).nickname).toBe('林老师');
    } finally { await studentContext.close(); }
    await page.locator('[data-group-invite-open]').click();
    await page.locator(`[data-invite-identity="${seed.inviteIdentity}"] input`).check();
    await page.locator('[data-group-invite-submit]').click();
    await expect(page.locator('#social-group-invite')).toBeHidden();
    await expect(page.locator('.social-member')).toHaveCount(3);
    await page.screenshot({ path: test.info().outputPath('live-group-desktop.png') });
    expect(errors).toEqual([]);
});

test('LQ live mobile dark chat and group settings remain within the viewport', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 });
    await login(page);
    await chat(page);
    await page.evaluate(() => {
        document.documentElement.dataset.appearance = 'dark';
        document.documentElement.dataset.lqSceneTone = 'dark';
    });
    const toggle = page.locator('[data-chat-toggle="list"]');
    if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
    await page.locator(`[data-conv="group:${fixture.socialChatSeed.groupId}"]`).click();
    await page.locator('[data-chat-toggle="side"]').click();
    await expect(page.locator('[data-group-card]')).toHaveCount(3);
    await page.screenshot({ path: test.info().outputPath('live-group-mobile-dark.png') });
    await page.locator('[data-group-settings-open]').click();
    await expect(page.locator('#social-group-settings')).toBeVisible();
    const bounds = await page.locator('#social-group-settings .lq-dialog__surface').boundingBox();
    expect(bounds?.x).toBeGreaterThanOrEqual(0);
    expect((bounds?.x || 0) + (bounds?.width || 0)).toBeLessThanOrEqual(361);
    await page.screenshot({ path: test.info().outputPath('live-settings-mobile-dark.png') });
    const save = page.locator('[data-group-private-form]').getByRole('button', { name: '保存我的设置', exact: true });
    await save.scrollIntoViewIfNeeded();
    const saveBox = await save.boundingBox(), fab = await page.locator('#ai-chat-fab').boundingBox();
    if (saveBox && fab) expect(saveBox.x + saveBox.width <= fab.x || saveBox.x >= fab.x + fab.width
        || saveBox.y + saveBox.height <= fab.y || saveBox.y >= fab.y + fab.height).toBeTruthy();
    await page.screenshot({ path: test.info().outputPath('live-settings-mobile-actions.png') });
    await page.keyboard.press('Escape');
    await expect(page.locator('#social-group-settings')).toBeHidden();
});

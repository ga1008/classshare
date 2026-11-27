import { test, expect, type Locator, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

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

// The seed provides no uploaded avatar. A profile-name SVG, when present,
// covers the LQ fallback, so checking the fallback text alone misses leaks.
async function avatarInitials(avatar: Locator) {
    return avatar.evaluate(async node => {
        const image = node.querySelector<HTMLImageElement>('img.lq-avatar__image');
        const fallback = node.querySelector('.lq-avatar__fallback')?.textContent || '';
        if (!image) return fallback;
        const response = await fetch(image.src);
        const svg = new DOMParser().parseFromString(await response.text(), 'image/svg+xml');
        const initials = svg.querySelector('text')?.textContent;
        if (initials) return initials;
        await image.decode();
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 32;
        const context = canvas.getContext('2d')!;
        context.drawImage(image, 0, 0, 32, 32);
        const pixels = context.getImageData(0, 0, 32, 32).data;
        const transparent = pixels.every((channel, index) => index % 4 !== 3 || channel === 0);
        return transparent ? fallback : '';
    });
}

test('LQ live upper-left shortcut sends and withdraws a friend request', async ({ page }) => {
    await login(page);
    await chat(page);
    const plus = page.locator('[data-social-chat-add]');
    await plus.click();
    await expect(page.locator('#social-friends')).toBeHidden();
    await page.locator('#social-user-search').fill(fixture.otherTeacher.email);
    const identity = `teacher:${fixture.otherTeacher.id}`;
    const result = page.locator('.social-results li').filter({ has: page.locator(`[data-user-card="${identity}"]`) });
    const add = result.getByRole('button', { name: '加好友' });
    await page.locator('#social-request-message').fill('一起备课');
    await add.click();
    await expect(result.getByRole('button', { name: '已申请' })).toBeDisabled();
    const outgoing = page.locator('.social-add__outgoing li').filter({ has: page.locator(`[data-user-card="${identity}"]`) });
    await expect(outgoing).toContainText('一起备课');
    await outgoing.getByRole('button', { name: '撤回' }).click();
    await expect(outgoing).toHaveCount(0);
    await expect(add).toBeEnabled();
    const data = await (await page.request.get(`${origin}/api/social/friends/requests`)).json();
    expect(data.outgoing.some((item: any) => item.user.identity === identity)).toBe(false);
    await page.keyboard.press('Escape');
    await expect(plus).toBeFocused();
});

test('LQ live student nickname updates open group messages and quotes for another member', async ({ page: teacher, browser }, info) => {
    const studentContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    try {
        const student = await studentContext.newPage();
        await login(teacher);
        await login(student, true);
        const { groupId, studentIdentity } = fixture.socialChatSeed;
        const groupUrl = `${origin}/api/social/groups/${groupId}`;
        const messagesUrl = `${origin}/api/social/conversations/group:${groupId}/messages`;
        const reset = await student.request.patch(`${groupUrl}/me`, { data: { nickname: '' } });
        expect(reset.ok()).toBeTruthy();
        const group = await (await teacher.request.get(groupUrl)).json();
        const name = group.members.find((item: any) => item.identity === studentIdentity).name;
        const send = async (page: Page, body: string, replyTo?: number) => {
            const response = await page.request.post(messagesUrl, { data: {
                body, client_nonce: randomUUID(), ...(replyTo ? { reply_to_id: replyTo } : {}),
            } });
            expect(response.ok(), await response.text()).toBeTruthy();
            return (await response.json()).id;
        };
        const original = await send(student, '这道合成题我来讲解');
        const reply = await send(teacher, '好，我们接着讨论', original);
        const openGroup = async (page: Page) => {
            await chat(page);
            await page.locator(`[data-conv="group:${groupId}"]`).click();
            await expect(page.locator('[data-group-card=members]')).toBeVisible();
        };
        await openGroup(teacher);
        await openGroup(student);
        const author = teacher.locator(`[data-message-id="${original}"] .lq-bubble__author`);
        const quote = teacher.locator(`[data-message-id="${reply}"] .social-quote strong`);
        const member = teacher.locator(`[data-social-member-name][data-user-card="${studentIdentity}"]`);
        const avatars = [
            teacher.locator(`[data-message-id="${original}"] .social-msg__avatar`),
            teacher.locator('.social-member').filter({ has: member }).locator('.social-avatar'),
        ];
        const expectInitials = async (displayName: string) => {
            for (const avatar of avatars) await expect.poll(async () => {
                const initials = await avatarInitials(avatar);
                return initials.length > 0 && displayName.includes(initials);
            }).toBe(true);
        };
        await expect(author).toHaveText(name);
        await expect(quote).toHaveText(name);
        await expectInitials(name);
        const messageNode = await teacher.locator(`[data-message-id="${original}"]`).elementHandle();
        const changeNickname = async (nickname: string) => {
            await student.locator('[data-group-settings-open]').click();
            await student.locator('#social-group-nickname').fill(nickname);
            await student.locator('[data-group-private-form]').getByRole('button', { name: '保存我的设置', exact: true }).click();
            await expect(student.locator('#social-group-settings')).toContainText('我的设置已保存');
            await student.keyboard.press('Escape');
            await expect(student.locator('[data-group-card=members]')).toContainText(`${nickname || name}（我）`);
        };
        await changeNickname('折纸星星');
        await expect(author).toHaveText('折纸星星');
        await expect(quote).toHaveText('折纸星星');
        await expect(member).toHaveText('折纸星星');
        await expectInitials('折纸星星');
        expect(await messageNode!.evaluate(node => node.isConnected)).toBe(true);
        await messageNode!.dispose();
        await teacher.screenshot({ path: info.outputPath('group-member-nickname.png') });
        const next = await send(student, '改名后再补充一条');
        await expect(teacher.locator(`[data-message-id="${next}"] .lq-bubble__author`)).toHaveText('折纸星星');
        await openGroup(teacher);
        await expect(author).toHaveText('折纸星星');
        await expect(quote).toHaveText('折纸星星');
        await changeNickname('');
        await expect(author).toHaveText(name);
        await expect(quote).toHaveText(name);
        await expect(member).toHaveText(name);
        await expectInitials(name);
    } finally {
        await studentContext.close();
    }
});

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

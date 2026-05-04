function clampPercent(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return 0;
    return Math.max(0, Math.min(100, Math.round(number)));
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char]));
}

function clearRevealCookie() {
    document.cookie = 'cultivation_reveal=; Max-Age=0; path=/; SameSite=Lax';
}

function shouldRevealFromCookie() {
    return document.cookie
        .split(';')
        .map((item) => item.trim())
        .includes('cultivation_reveal=1');
}

function ensureRealmBadge(target, profile) {
    if (!target || !profile?.highest_level) return;
    let badge = target.querySelector('.cultivation-avatar-badge');
    if (!badge) {
        badge = document.createElement('span');
        badge.className = 'cultivation-avatar-badge';
        target.appendChild(badge);
    }
    badge.textContent = profile.highest_level.short_name || profile.highest_level.level_name || '入道';
}

export function applyCultivationIdentity(profile) {
    if (!profile?.highest_level) return;
    const theme = String(profile.avatar_theme || profile.highest_level.theme || 'mortal').replace(/[^a-z0-9_-]/gi, '') || 'mortal';
    document.body.dataset.cultivationTheme = theme;

    document.querySelectorAll('.profile-entry-button').forEach((button) => {
        button.classList.add('cultivation-avatar-frame');
        button.dataset.cultivationTheme = theme;
        button.title = `${profile.address_name || profile.student_name || '个人中心'} · ${profile.highest_level.level_name}`;
        ensureRealmBadge(button, profile);
    });

    document.querySelectorAll('.profile-hero__avatar-ring').forEach((node) => {
        node.classList.add('cultivation-avatar-frame', 'cultivation-avatar-frame--large');
        node.dataset.cultivationTheme = theme;
        ensureRealmBadge(node, profile);
    });
}

function buildReveal(profile, durationMs) {
    const level = profile?.highest_level || {};
    const progress = clampPercent(profile?.progress_percent);
    const theme = String(profile?.avatar_theme || level.theme || 'mortal').replace(/[^a-z0-9_-]/gi, '') || 'mortal';
    const kicker = profile?.breakthrough_ready
        ? '破境已至'
        : (profile?.generating_stage_exam ? '试炼生成中' : (level.aura_label || '灵根初醒'));
    const levelText = profile?.breakthrough_ready && profile?.next_stage_name
        ? `可破境 · ${profile.next_stage_name}`
        : (level.level_name || '未入道');
    const courseLine = profile?.reveal_subtitle
        || `${profile?.best_course?.course_name || '课堂修行'} · 修为 ${profile?.score ?? 0} / 100`;
    const progressLabel = profile?.progress_label || '修为进度';
    const nextHint = profile?.breakthrough_ready && profile?.next_stage_name
        ? `已可挑战 ${profile.next_stage_name}`
        : (profile?.generating_stage_exam && profile?.next_stage_name
            ? `${profile.next_stage_name} 试炼正在生成`
            : (profile?.next_stage_name ? `距 ${profile.next_stage_name} 继续凝练` : '当前境界已点亮'));
    const overlay = document.createElement('div');
    overlay.className = 'cultivation-login-reveal';
    overlay.setAttribute('role', 'status');
    overlay.setAttribute('aria-live', 'polite');
    overlay.innerHTML = `
        <div class="cultivation-login-reveal__field" aria-hidden="true"></div>
        <section class="cultivation-login-reveal__card" data-cultivation-theme="${theme}">
            <div class="cultivation-login-reveal__sigil" aria-hidden="true">
                <span></span>
            </div>
            <p class="cultivation-login-reveal__kicker">${escapeHtml(kicker)}</p>
            <h1>${escapeHtml(profile?.address_name || profile?.student_name || '道友')}</h1>
            <strong>${escapeHtml(levelText)}</strong>
            <p>${escapeHtml(courseLine)}</p>
            <div class="cultivation-login-reveal__bar" aria-label="${escapeHtml(progressLabel)}">
                <span style="width: ${progress}%"></span>
            </div>
            <small>${escapeHtml(nextHint)}</small>
        </section>
    `;
    overlay.style.setProperty('--cultivation-reveal-duration', `${durationMs}ms`);
    return overlay;
}

export function playCultivationReveal(profile, options = {}) {
    const durationMs = Math.max(3000, Math.min(5000, Number(options.durationMs || 3600)));
    const onDone = typeof options.onDone === 'function' ? options.onDone : null;
    if (!profile?.highest_level) {
        window.setTimeout(() => onDone?.(), 450);
        return;
    }

    const overlay = buildReveal(profile, durationMs);
    document.body.appendChild(overlay);
    document.body.classList.add('has-cultivation-login-reveal');
    window.requestAnimationFrame(() => overlay.classList.add('is-open'));

    window.setTimeout(() => {
        overlay.classList.add('is-closing');
        window.setTimeout(() => {
            overlay.remove();
            document.body.classList.remove('has-cultivation-login-reveal');
            onDone?.();
        }, 320);
    }, durationMs);
}

async function fetchCultivationProfile() {
    try {
        const response = await fetch('/api/learning/cultivation-profile', {
            credentials: 'same-origin',
            headers: { Accept: 'application/json' },
        });
        if (!response.ok) return null;
        const payload = await response.json();
        return payload?.profile || null;
    } catch (error) {
        return null;
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    if (document.body?.dataset.authenticatedUser !== 'true') {
        return;
    }
    const profile = await fetchCultivationProfile();
    if (!profile) return;
    window.CULTIVATION_PROFILE = profile;
    applyCultivationIdentity(profile);
    if (shouldRevealFromCookie()) {
        clearRevealCookie();
        playCultivationReveal(profile, { durationMs: 3400 });
    }
});

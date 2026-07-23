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

// ── 人生一言（游戏加载屏式登录提示） ──────────────────────────────

const TIP_SEEN_STORAGE_KEY = 'lanshareLifeTipSeen';
const TIP_SEEN_LIMIT = 20;
const TIP_BASE_DURATION_MS = 2800;
const TIP_PER_CHAR_MS = 80;
const TIP_MIN_DURATION_MS = 3000;
const TIP_MAX_DURATION_MS = 8000;
const TIP_IMAGE_WAIT_MS = 600;

function readSeenTipIds() {
    try {
        const raw = window.localStorage.getItem(TIP_SEEN_STORAGE_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
        return [];
    }
}

function rememberSeenTip(tipId) {
    try {
        const seen = readSeenTipIds().filter((id) => id !== tipId);
        seen.push(tipId);
        window.localStorage.setItem(
            TIP_SEEN_STORAGE_KEY,
            JSON.stringify(seen.slice(-TIP_SEEN_LIMIT)),
        );
    } catch (error) {
        // localStorage 不可用（隐私模式等）时静默放弃去重。
    }
}

function chooseTip(loginTip) {
    const tips = Array.isArray(loginTip?.tips) ? loginTip.tips.filter((tip) => tip && tip.text) : [];
    if (!tips.length) return null;
    const seen = new Set(readSeenTipIds());
    return tips.find((tip) => !seen.has(tip.id)) || tips[0];
}

function tipDurationMs(text) {
    const length = String(text || '').length;
    return Math.max(TIP_MIN_DURATION_MS, Math.min(TIP_MAX_DURATION_MS, TIP_BASE_DURATION_MS + length * TIP_PER_CHAR_MS));
}

function preloadImage(url, timeoutMs) {
    return new Promise((resolve) => {
        if (!url) {
            resolve(false);
            return;
        }
        const image = new Image();
        const timer = window.setTimeout(() => resolve(false), timeoutMs);
        image.onload = () => {
            window.clearTimeout(timer);
            resolve(true);
        };
        image.onerror = () => {
            window.clearTimeout(timer);
            resolve(false);
        };
        image.src = url;
    });
}

function buildIdentityChip(profile) {
    const level = profile?.highest_level;
    if (!level) return '';
    const progress = clampPercent(profile?.progress_percent);
    const theme = String(profile?.avatar_theme || level.theme || 'mortal').replace(/[^a-z0-9_-]/gi, '') || 'mortal';
    const levelText = profile?.breakthrough_ready && profile?.next_stage_name
        ? `可破境 · ${profile.next_stage_name}`
        : (level.level_name || '未入道');
    const name = profile?.address_name || profile?.student_name || '修士';
    return `
        <header class="life-tip-identity" data-cultivation-theme="${theme}">
            <span class="life-tip-identity__sigil" aria-hidden="true"></span>
            <span class="life-tip-identity__name">${escapeHtml(name)}</span>
            <span class="life-tip-identity__level">${escapeHtml(levelText)}</span>
            <span class="life-tip-identity__bar" aria-hidden="true"><i style="width: ${progress}%"></i></span>
        </header>
    `;
}

function buildTipReveal(profile, tip, durationMs, hasImage) {
    const overlay = document.createElement('div');
    overlay.className = 'cultivation-login-reveal cultivation-login-reveal--tip';
    overlay.setAttribute('role', 'status');
    overlay.setAttribute('aria-live', 'polite');
    overlay.dataset.tipCategory = tip.category || '';
    overlay.innerHTML = `
        <div class="life-tip-backdrop${hasImage ? ' has-image' : ''}" aria-hidden="true"
            ${hasImage ? `style="background-image: url('${escapeHtml(tip.image_url)}')"` : ''}></div>
        <div class="life-tip-vignette" aria-hidden="true"></div>
        ${buildIdentityChip(profile)}
        <section class="life-tip-stage">
            <p class="life-tip-stage__kicker">${escapeHtml(tip.category || '人生提示')}</p>
            <p class="life-tip-stage__text" data-life-tip-text aria-label="${escapeHtml(tip.text)}"></p>
            ${tip.source_ref ? `<p class="life-tip-stage__source">—— ${escapeHtml(tip.source_ref)}</p>` : ''}
        </section>
        <footer class="life-tip-footer">
            <span class="life-tip-footer__timer" aria-hidden="true"><i style="animation-duration: ${durationMs}ms"></i></span>
            <div class="life-tip-footer__actions">
                <button type="button" class="life-tip-feedback" data-life-tip-feedback="1">👍 有用</button>
                <button type="button" class="life-tip-feedback" data-life-tip-feedback="-1">👎 无感</button>
                <span class="life-tip-footer__skip">点击任意处跳过 ›</span>
            </div>
        </footer>
    `;
    return overlay;
}

function wireTipFeedback(overlay, tip) {
    overlay.querySelectorAll('[data-life-tip-feedback]').forEach((button) => {
        button.addEventListener('click', async (event) => {
            // 反馈不算"跳过"：拦住冒泡，让提示继续展示。
            event.stopPropagation();
            if (button.disabled) return;
            const verdict = Number(button.dataset.lifeTipFeedback) || 1;
            overlay.querySelectorAll('[data-life-tip-feedback]').forEach((peer) => {
                peer.disabled = true;
                peer.classList.remove('is-chosen');
            });
            button.classList.add('is-chosen');
            try {
                await fetch('/api/learning/life-tips/feedback', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tip_id: tip.id, verdict }),
                });
            } catch (error) {
                // 反馈是锦上添花，失败静默。
            }
        });
    });
}

function typewrite(node, text, durationMs, reducedMotion) {
    if (!node) return;
    if (reducedMotion) {
        node.textContent = text;
        return;
    }
    const chars = Array.from(String(text || ''));
    // 打字占总时长的前 55%，留足静读时间。
    const stepMs = Math.max(18, Math.floor((durationMs * 0.55) / Math.max(1, chars.length)));
    let index = 0;
    const timer = window.setInterval(() => {
        index += 1;
        node.textContent = chars.slice(0, index).join('');
        if (index >= chars.length) window.clearInterval(timer);
    }, stepMs);
}

function playLifeTipReveal(profile, tip, onDone) {
    const durationMs = tipDurationMs(tip.text);
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches || false;

    preloadImage(tip.image_url, TIP_IMAGE_WAIT_MS).then((hasImage) => {
        const overlay = buildTipReveal(profile, tip, durationMs, hasImage);
        document.body.appendChild(overlay);
        document.documentElement.classList.add('has-cultivation-login-reveal');
        document.body.classList.add('has-cultivation-login-reveal');
        window.requestAnimationFrame(() => overlay.classList.add('is-open'));
        rememberSeenTip(tip.id);

        let finished = false;
        let autoTimer = null;
        const finish = () => {
            if (finished) return;
            finished = true;
            if (autoTimer) window.clearTimeout(autoTimer);
            window.removeEventListener('keydown', onKeydown, true);
            overlay.classList.add('is-closing');
            window.setTimeout(() => {
                overlay.remove();
                document.documentElement.classList.remove('has-cultivation-login-reveal');
                document.body.classList.remove('has-cultivation-login-reveal');
                onDone?.();
            }, 320);
        };
        const onKeydown = () => finish();

        overlay.addEventListener('click', finish);
        window.addEventListener('keydown', onKeydown, true);
        autoTimer = window.setTimeout(finish, durationMs);

        wireTipFeedback(overlay, tip);
        typewrite(overlay.querySelector('[data-life-tip-text]'), tip.text, durationMs, reducedMotion);
    });
}

// ── 纯修为卡（无提示语时的兜底展示） ──────────────────────────────

function buildReveal(profile, durationMs) {
    const level = profile?.highest_level || {};
    const progress = clampPercent(profile?.progress_percent);
    const theme = String(profile?.avatar_theme || level.theme || 'mortal').replace(/[^a-z0-9_-]/gi, '') || 'mortal';
    const rankNotice = profile?.rank_notice || profile?.best_course?.rank_notice || null;
    const rankTier = String(rankNotice?.tier || 'middle').replace(/[^a-z0-9_-]/gi, '') || 'middle';
    const rankLine = rankNotice?.message || '';
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
            <h1>${escapeHtml(profile?.address_name || profile?.student_name || '修士')}</h1>
            <strong>${escapeHtml(levelText)}</strong>
            <p>${escapeHtml(courseLine)}</p>
            ${rankLine ? `<p class="cultivation-login-reveal__rank" data-rank-tier="${escapeHtml(rankTier)}">${escapeHtml(rankLine)}</p>` : ''}
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
    const onDone = typeof options.onDone === 'function' ? options.onDone : null;
    const tip = chooseTip(options.loginTip);
    // 有提示就播加载屏（教师无修为 profile 也能播，只是没有徽章条）。
    if (tip) {
        playLifeTipReveal(profile, tip, onDone);
        return;
    }
    if (!profile?.highest_level) {
        window.setTimeout(() => onDone?.(), 450);
        return;
    }

    const durationMs = Math.max(3000, Math.min(5000, Number(options.durationMs || 3600)));
    const overlay = buildReveal(profile, durationMs);
    document.body.appendChild(overlay);
    document.documentElement.classList.add('has-cultivation-login-reveal');
    document.body.classList.add('has-cultivation-login-reveal');
    window.requestAnimationFrame(() => overlay.classList.add('is-open'));

    window.setTimeout(() => {
        overlay.classList.add('is-closing');
        window.setTimeout(() => {
            overlay.remove();
            document.documentElement.classList.remove('has-cultivation-login-reveal');
            document.body.classList.remove('has-cultivation-login-reveal');
            onDone?.();
        }, 320);
    }, durationMs);
}

async function fetchCultivationProfile(includeTip) {
    try {
        const url = includeTip
            ? '/api/learning/cultivation-profile?include_tip=1'
            : '/api/learning/cultivation-profile';
        const response = await fetch(url, {
            credentials: 'same-origin',
            headers: { Accept: 'application/json' },
        });
        if (!response.ok) return null;
        return await response.json();
    } catch (error) {
        return null;
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    if (document.body?.dataset.authenticatedUser !== 'true') {
        return;
    }
    const wantsReveal = shouldRevealFromCookie();
    const payload = await fetchCultivationProfile(wantsReveal);
    const profile = payload?.profile || null;
    if (profile) {
        window.CULTIVATION_PROFILE = profile;
        applyCultivationIdentity(profile);
    }
    // 教师没有修为 profile，但 login_tip 存在时同样播放提示屏。
    if (wantsReveal && (profile || payload?.login_tip)) {
        clearRevealCookie();
        playCultivationReveal(profile, { durationMs: 3400, loginTip: payload?.login_tip || null });
    }
});

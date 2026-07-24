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
                <button type="button" class="life-tip-feedback" data-life-tip-save>💾 保存</button>
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

const SAVE_CARD_WIDTH = 1600;
const SAVE_CARD_HEIGHT = 900;
const SAVE_FONT_STACK = '"PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif';

function wrapCanvasText(ctx, text, maxWidth) {
    const chars = Array.from(String(text || ''));
    const lines = [];
    let current = '';
    chars.forEach((char) => {
        if (ctx.measureText(current + char).width > maxWidth && current) {
            lines.push(current);
            current = char;
        } else {
            current += char;
        }
    });
    if (current) lines.push(current);
    return lines;
}

async function saveTipCard(tip, hasImage) {
    const canvas = document.createElement('canvas');
    canvas.width = SAVE_CARD_WIDTH;
    canvas.height = SAVE_CARD_HEIGHT;
    const ctx = canvas.getContext('2d');

    let imageDrawn = false;
    if (hasImage && tip.image_url) {
        try {
            const image = await new Promise((resolve, reject) => {
                const node = new Image();
                node.onload = () => resolve(node);
                node.onerror = reject;
                node.src = tip.image_url;
            });
            const scale = Math.max(SAVE_CARD_WIDTH / image.width, SAVE_CARD_HEIGHT / image.height);
            const width = image.width * scale;
            const height = image.height * scale;
            ctx.drawImage(image, (SAVE_CARD_WIDTH - width) / 2, (SAVE_CARD_HEIGHT - height) / 2, width, height);
            imageDrawn = true;
        } catch (error) {
            // 图片取不到时退回渐变底。
        }
    }
    if (!imageDrawn) {
        const gradient = ctx.createRadialGradient(320, 90, 100, 800, 450, 1200);
        gradient.addColorStop(0, '#1e293b');
        gradient.addColorStop(0.5, '#0f172a');
        gradient.addColorStop(1, '#020617');
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, SAVE_CARD_WIDTH, SAVE_CARD_HEIGHT);
    }

    // 与展示层一致的压暗遮罩，保证白字对比度。
    ctx.fillStyle = 'rgba(2, 6, 23, 0.5)';
    ctx.fillRect(0, 0, SAVE_CARD_WIDTH, SAVE_CARD_HEIGHT);

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    ctx.font = `600 30px ${SAVE_FONT_STACK}`;
    ctx.fillStyle = '#7dd3fc';
    const category = tip.category || '人生提示';
    ctx.fillText(`—  ${Array.from(category).join(' ')}  —`, SAVE_CARD_WIDTH / 2, 320);

    ctx.font = `600 46px ${SAVE_FONT_STACK}`;
    const lines = wrapCanvasText(ctx, tip.text, SAVE_CARD_WIDTH * 0.72);
    const lineHeight = 82;
    const startY = SAVE_CARD_HEIGHT / 2 - ((lines.length - 1) * lineHeight) / 2 + 20;
    ctx.fillStyle = '#f8fafc';
    ctx.shadowColor = 'rgba(2, 6, 23, 0.85)';
    ctx.shadowBlur = 18;
    lines.forEach((line, index) => {
        ctx.fillText(line, SAVE_CARD_WIDTH / 2, startY + index * lineHeight);
    });

    const link = document.createElement('a');
    link.download = `人生一言-${category}-${tip.id || ''}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
}

function wireTipSave(overlay, tip, hasImage) {
    const button = overlay.querySelector('[data-life-tip-save]');
    if (!button) return;
    button.addEventListener('click', async (event) => {
        // 保存不算"跳过"。
        event.stopPropagation();
        if (button.disabled) return;
        button.disabled = true;
        try {
            await saveTipCard(tip, hasImage);
            button.textContent = '✅ 已保存';
        } catch (error) {
            button.textContent = '保存失败';
        }
    });
}

function revealTipText(node, text, durationMs, reducedMotion) {
    if (!node) return;
    node.textContent = text;
    if (reducedMotion) return;
    // 整句渐显（收场时随 overlay 一起渐隐），比逐字打出更从容。
    node.classList.add('life-tip-stage__text--fade');
    node.style.setProperty('--life-tip-fade-out-delay', `${Math.max(0, durationMs - 900)}ms`);
}

function preloadDuringReveal(loginTipCandidates) {
    // 展示的三四秒里顺手把其余候选背景图拉进缓存：本次跳过后
    // 或下次登录换句时图片零等待。全部走浏览器缓存，服务器无感。
    const idle = window.requestIdleCallback || ((fn) => window.setTimeout(fn, 300));
    idle(() => {
        (loginTipCandidates || []).forEach((tip) => {
            if (tip?.image_url) {
                const img = new Image();
                img.src = tip.image_url;
            }
        });
    });
}

function playLifeTipReveal(profile, tip, onDone, otherCandidates) {
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
        wireTipSave(overlay, tip, hasImage);
        revealTipText(overlay.querySelector('[data-life-tip-text]'), tip.text, durationMs, reducedMotion);
        preloadDuringReveal(otherCandidates);
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
        const others = (options.loginTip?.tips || []).filter((item) => item && item.id !== tip.id);
        playLifeTipReveal(profile, tip, onDone, others);
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

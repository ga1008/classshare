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
const TIP_MIN_DURATION_MS = 5000;
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
    // 成功时 resolve 加载完的 Image 元素（truthy），供亮度采样复用。
    return new Promise((resolve) => {
        if (!url) {
            resolve(false);
            return;
        }
        const image = new Image();
        const timer = window.setTimeout(() => resolve(false), timeoutMs);
        image.onload = () => {
            window.clearTimeout(timer);
            resolve(image);
        };
        image.onerror = () => {
            window.clearTimeout(timer);
            resolve(false);
        };
        image.src = url;
    });
}

const TONE_LUMA_THRESHOLD = 148;

export function sampleImageTone(image) {
    // 采样图片中央横带（文字所在区域）的平均亮度：亮 → 深色字，暗 → 白字。
    try {
        const canvas = document.createElement('canvas');
        canvas.width = 48;
        canvas.height = 27;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(image, 0, 0, 48, 27);
        const data = ctx.getImageData(6, 8, 36, 11).data;
        let sum = 0;
        for (let i = 0; i < data.length; i += 4) {
            sum += 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
        }
        return sum / (data.length / 4) > TONE_LUMA_THRESHOLD ? 'light' : 'dark';
    } catch (error) {
        return 'dark';
    }
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

function buildTipReveal(profile, tip, durationMs, hasImage, tone, imageUrl) {
    const overlay = document.createElement('div');
    overlay.className = 'cultivation-login-reveal cultivation-login-reveal--tip';
    overlay.setAttribute('role', 'status');
    overlay.setAttribute('aria-live', 'polite');
    overlay.dataset.tipCategory = tip.category || '';
    overlay.dataset.tipTone = tone === 'light' ? 'light' : 'dark';
    overlay.innerHTML = `
        <div class="life-tip-backdrop${hasImage ? ' has-image' : ''}" aria-hidden="true"
            ${hasImage ? `style="background-image: url('${escapeHtml(imageUrl || tip.image_url)}')"` : ''}></div>
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
                <span class="life-tip-footer__skip" data-life-tip-skip>点击任意处继续 ›</span>
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

    // 色调自适应液态玻璃卡：亮图 → 白玻璃深字，暗图 → 黑玻璃白字。
    const tone = imageDrawn ? sampleImageTone(canvas) : 'dark';
    const isLight = tone === 'light';

    ctx.font = `500 22px ${SAVE_FONT_STACK}`;
    const lines = wrapCanvasText(ctx, tip.text, SAVE_CARD_WIDTH * 0.46);
    const lineHeight = 40;
    const category = tip.category || '人生提示';

    const cardWidth = SAVE_CARD_WIDTH * 0.54;
    const cardHeight = 96 + lines.length * lineHeight + (tip.source_ref ? 38 : 0) + 24;
    const cardX = (SAVE_CARD_WIDTH - cardWidth) / 2;
    const cardY = (SAVE_CARD_HEIGHT - cardHeight) / 2;
    const radius = 28;

    const roundedPath = () => {
        ctx.beginPath();
        ctx.roundRect(cardX, cardY, cardWidth, cardHeight, radius);
    };

    // 玻璃：圆角裁剪内重画一遍模糊背景 + 半透明色调层 + 细描边
    ctx.save();
    roundedPath();
    ctx.clip();
    if (imageDrawn) {
        ctx.filter = 'blur(30px)';
        ctx.drawImage(canvas, 0, 0);
        ctx.filter = 'none';
    }
    ctx.fillStyle = isLight ? 'rgba(255, 255, 255, 0.4)' : 'rgba(8, 14, 30, 0.34)';
    ctx.fillRect(cardX, cardY, cardWidth, cardHeight);
    ctx.restore();
    roundedPath();
    ctx.strokeStyle = isLight ? 'rgba(255, 255, 255, 0.85)' : 'rgba(255, 255, 255, 0.2)';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    ctx.font = `500 14px ${SAVE_FONT_STACK}`;
    ctx.fillStyle = isLight ? '#0369a1' : '#7dd3fc';
    ctx.fillText(`—  ${Array.from(category).join(' ')}  —`, SAVE_CARD_WIDTH / 2, cardY + 48);

    ctx.font = `500 22px ${SAVE_FONT_STACK}`;
    ctx.fillStyle = isLight ? '#0f172a' : '#f8fafc';
    const startY = cardY + 96 + lineHeight / 2 - 12;
    lines.forEach((line, index) => {
        ctx.fillText(line, SAVE_CARD_WIDTH / 2, startY + index * lineHeight);
    });
    if (tip.source_ref) {
        ctx.font = `400 13px ${SAVE_FONT_STACK}`;
        ctx.fillStyle = isLight ? '#64748b' : '#94a3b8';
        ctx.fillText(`—— ${tip.source_ref}`, SAVE_CARD_WIDTH / 2, cardY + cardHeight - 32);
    }

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

function playLifeTipReveal(profile, tip, onDone, otherCandidates, scene = null) {
    const durationMs = tipDurationMs(tip.text);
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches || false;

    // 场景模式：登录页已预载并展示同一张背景图，直接复用，保证"背景图不变"。
    const readyPromise = scene?.imageUrl
        ? Promise.resolve(true)
        : preloadImage(tip.image_url, TIP_IMAGE_WAIT_MS);

    readyPromise.then((loaded) => {
        const hasImage = Boolean(scene?.imageUrl || loaded);
        const imageUrl = scene?.imageUrl || (hasImage ? tip.image_url : null);
        const tone = scene?.tone || (loaded && loaded !== true ? sampleImageTone(loaded) : 'dark');
        const overlay = buildTipReveal(profile, tip, durationMs, hasImage, tone, imageUrl);
        if (scene) overlay.classList.add('cultivation-login-reveal--scene');
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
            const topbar = document.querySelector('.app-topbar');
            if (hasImage && imageUrl && topbar && !scene) {
                // 首页 cookie 播放路径：背景不散场，收缩进顶栏成为菜单背景。
                collapseRevealToTopbar(overlay, imageUrl, tip.text, onDone);
                return;
            }
            if (hasImage && imageUrl && scene) {
                // 登录页场景路径：把背景交棒给首页，由首页完成收缩动画。
                writeSceneHandoff({ image: imageUrl, tip: tip.text, t: Date.now() });
            }
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
        const stage = overlay.querySelector('.life-tip-stage');
        // 点玻璃卡不算跳过：想多读几秒、长按选中复制句子都不被打断。
        stage?.addEventListener('click', (event) => {
            event.stopPropagation();
        });
        window.addEventListener('keydown', onKeydown, true);

        // 悬停玻璃卡暂停计时：读完再走，移开自动续走剩余时长。
        const skipLabel = overlay.querySelector('[data-life-tip-skip]');
        let remainingMs = durationMs;
        let timerStartedAt = Date.now();
        const startTimer = () => {
            timerStartedAt = Date.now();
            autoTimer = window.setTimeout(finish, remainingMs);
        };
        const pauseTimer = () => {
            if (finished || !autoTimer) return;
            window.clearTimeout(autoTimer);
            autoTimer = null;
            remainingMs = Math.max(600, remainingMs - (Date.now() - timerStartedAt));
            overlay.classList.add('is-paused');
            if (skipLabel) skipLabel.textContent = '静静读完 · 点击任意处继续 ›';
            // 阅读中文字定格为完全可见：取消渐显/渐隐动画，避免悬停时句子仍隐形或变暗。
            const textNode = overlay.querySelector('[data-life-tip-text]');
            if (textNode) {
                textNode.style.animation = 'none';
                textNode.style.opacity = '1';
                textNode.style.filter = 'none';
                textNode.style.transform = 'none';
            }
        };
        const resumeTimer = () => {
            if (finished || autoTimer) return;
            overlay.classList.remove('is-paused');
            if (skipLabel) skipLabel.textContent = '点击任意处继续 ›';
            startTimer();
        };
        stage?.addEventListener('mouseenter', pauseTimer);
        stage?.addEventListener('mouseleave', resumeTimer);
        startTimer();

        wireTipFeedback(overlay, tip);
        wireTipSave(overlay, tip, hasImage);
        if (scene?.fromRect && !reducedMotion) {
            morphStageFromRect(overlay, scene.fromRect);
        }
        revealTipText(overlay.querySelector('[data-life-tip-text]'), tip.text, durationMs, reducedMotion);
        preloadDuringReveal(otherCandidates);
    });
}

// ── 登录场景交棒（2026-08-03）：登录页背景 → 一言玻璃卡 → 首页顶栏 ──────

const SCENE_HANDOFF_KEY = 'lanshareLoginScene';
const TOPBAR_SCENE_KEY = 'lanshareTopbarScene';
const SCENE_HANDOFF_MAX_AGE_MS = 45000;
const SCENE_COLLAPSE_MS = 820;

function readSessionJson(key) {
    try {
        const raw = window.sessionStorage.getItem(key);
        return raw ? JSON.parse(raw) : null;
    } catch (error) {
        return null;
    }
}

function writeSessionJson(key, value) {
    try {
        window.sessionStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
        // sessionStorage 不可用时放弃交棒，首页回落普通顶栏。
    }
}

function writeSceneHandoff(state) {
    writeSessionJson(SCENE_HANDOFF_KEY, state);
}

function persistTopbarScene(state) {
    writeSessionJson(TOPBAR_SCENE_KEY, { ...state, t: Date.now() });
}

// 表单卡 → 一言玻璃卡的近似 FLIP 变形：从登录卡的位置/大小生长到居中。
function morphStageFromRect(overlay, fromRect) {
    const stage = overlay.querySelector('.life-tip-stage');
    if (!stage || !fromRect || !fromRect.width) return;
    stage.classList.add('life-tip-stage--morph');
    window.requestAnimationFrame(() => {
        const to = stage.getBoundingClientRect();
        if (!to.width || !to.height) return;
        const dx = (fromRect.left + fromRect.width / 2) - (to.left + to.width / 2);
        const dy = (fromRect.top + fromRect.height / 2) - (to.top + to.height / 2);
        const sx = Math.max(0.2, fromRect.width / to.width);
        const sy = Math.max(0.2, fromRect.height / to.height);
        stage.style.transition = 'none';
        stage.style.transform = `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`;
        stage.style.opacity = '0.35';
        window.requestAnimationFrame(() => {
            stage.style.transition = '';
            stage.style.transform = '';
            stage.style.opacity = '';
        });
    });
}

function ensureTopbarSceneLayer(imageUrl) {
    const topbar = document.querySelector('.app-topbar');
    if (!topbar || !imageUrl) return;
    let layer = topbar.querySelector('.app-topbar__scene');
    if (!layer) {
        layer = document.createElement('div');
        layer.className = 'app-topbar__scene';
        layer.setAttribute('aria-hidden', 'true');
        layer.innerHTML = '<i></i><b></b>';
        topbar.prepend(layer);
    }
    const imageNode = layer.querySelector('i');
    if (imageNode) imageNode.style.backgroundImage = `url('${imageUrl}')`;
    document.documentElement.classList.add('has-topbar-scene');
}

function ensureTopbarChip(tipText) {
    const topbar = document.querySelector('.app-topbar');
    if (!topbar || topbar.querySelector('.topbar-scene-chip')) return;
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'topbar-scene-chip';
    chip.innerHTML = '<span class="topbar-scene-chip__dot" aria-hidden="true"></span><span class="topbar-scene-chip__label">人生一言</span>';
    const text = String(tipText || '').trim();
    if (text) {
        chip.title = text;
        chip.setAttribute('aria-label', `人生一言：${text}`);
        chip.addEventListener('click', (event) => {
            event.stopPropagation();
            const existing = document.querySelector('.topbar-scene-pop');
            if (existing) {
                existing.remove();
                return;
            }
            // 挂在 body 上做 fixed 定位，避开顶栏层叠上下文与继承样式。
            const pop = document.createElement('div');
            pop.className = 'topbar-scene-pop';
            const title = document.createElement('strong');
            title.className = 'topbar-scene-pop__title';
            title.textContent = '今日一言';
            const body = document.createElement('p');
            body.className = 'topbar-scene-pop__text';
            body.textContent = text;
            pop.append(title, body);
            const rect = chip.getBoundingClientRect();
            pop.style.top = `${Math.round(rect.bottom + 10)}px`;
            if (window.innerWidth < 560) {
                pop.style.left = '12px';
                pop.style.right = '12px';
            } else {
                pop.style.left = `${Math.round(Math.max(12, rect.left))}px`;
            }
            document.body.appendChild(pop);
            const close = () => {
                document.querySelector('.topbar-scene-pop')?.remove();
                document.removeEventListener('click', close);
                document.removeEventListener('keydown', onEsc);
            };
            const onEsc = (keyEvent) => {
                if (keyEvent.key === 'Escape') close();
            };
            window.setTimeout(() => {
                document.addEventListener('click', close);
                document.addEventListener('keydown', onEsc);
            }, 0);
        });
    } else {
        chip.disabled = true;
    }
    const brand = topbar.querySelector('.app-topbar-brand');
    if (brand) {
        brand.after(chip);
    } else {
        topbar.prepend(chip);
    }
}

// 首页 cookie 播放路径的收场：一言背景整体收缩进顶栏、化为模糊菜单背景。
function collapseRevealToTopbar(overlay, imageUrl, tipText, onDone) {
    const topbar = document.querySelector('.app-topbar');
    const rect = topbar.getBoundingClientRect();
    persistTopbarScene({ image: imageUrl, tip: tipText || '' });
    ensureTopbarSceneLayer(imageUrl);

    overlay.classList.add('is-scene-collapsing');
    const backdrop = overlay.querySelector('.life-tip-backdrop');
    if (backdrop) {
        backdrop.style.animation = 'none';
        backdrop.style.transition = `inset ${SCENE_COLLAPSE_MS}ms cubic-bezier(0.3, 0.7, 0.25, 1), filter ${SCENE_COLLAPSE_MS}ms ease, opacity ${SCENE_COLLAPSE_MS}ms ease`;
        window.requestAnimationFrame(() => {
            const bottomGap = Math.max(0, window.innerHeight - Math.max(56, rect.height + rect.top));
            backdrop.style.inset = `0 0 ${bottomGap}px 0`;
            backdrop.style.filter = 'blur(24px) saturate(1.1) brightness(1.04)';
            backdrop.style.opacity = '0';
        });
    }
    window.setTimeout(() => {
        overlay.remove();
        document.documentElement.classList.remove('has-cultivation-login-reveal');
        document.body.classList.remove('has-cultivation-login-reveal');
        ensureTopbarChip(tipText);
        onDone?.();
    }, SCENE_COLLAPSE_MS + 60);
}

// 场景选句：优先挑与登录背景图分类相配、且近期没看过的一句。
function chooseSceneTip(loginTip, scene) {
    const tips = Array.isArray(loginTip?.tips) ? loginTip.tips.filter((tip) => tip && tip.text) : [];
    if (!tips.length) return null;
    const seen = new Set(readSeenTipIds());
    const categories = new Set(scene?.categories || []);
    const unseen = tips.filter((tip) => !seen.has(tip.id));
    return unseen.find((tip) => categories.has(tip.category))
        || unseen[0]
        || tips.find((tip) => categories.has(tip.category))
        || tips[0];
}

/**
 * 登录页专用入口：表单卡原地变形为一言玻璃卡，结束后交棒给首页。
 */
export function playLoginSceneReveal(profile, options = {}) {
    const onDone = typeof options.onDone === 'function' ? options.onDone : null;
    const scene = options.scene || null;
    const card = options.fromElement || null;
    const fromRect = card?.getBoundingClientRect?.() || null;
    if (card) card.classList.add('login-card--handoff');
    clearRevealCookie();

    const tip = chooseSceneTip(options.loginTip, scene);
    if (!tip) {
        // 没有可播的一言：仍把背景交棒给首页，保持视觉连续。
        if (scene?.imageUrl) {
            writeSceneHandoff({ image: scene.imageUrl, tip: '', t: Date.now() });
        }
        window.setTimeout(() => onDone?.(), scene ? 560 : 420);
        return;
    }
    const others = (options.loginTip?.tips || []).filter((item) => item && item.id !== tip.id);
    playLifeTipReveal(profile, tip, onDone, others, scene ? {
        imageUrl: scene.imageUrl || null,
        tone: scene.tone || null,
        categories: scene.categories || [],
        fromRect,
    } : { imageUrl: null, tone: null, categories: [], fromRect });
}

// 首页开场：读取交棒状态，把满屏背景优雅收缩进顶栏。
function runSceneEntrance() {
    const root = document.documentElement;
    const handoff = readSessionJson(SCENE_HANDOFF_KEY);
    try {
        window.sessionStorage.removeItem(SCENE_HANDOFF_KEY);
    } catch (error) {
        // 忽略。
    }
    const hasCover = root.classList.contains('has-scene-cover');
    const fresh = handoff?.image && Date.now() - (handoff.t || 0) < SCENE_HANDOFF_MAX_AGE_MS;

    if (!fresh) {
        root.classList.remove('has-scene-cover');
        const persisted = readSessionJson(TOPBAR_SCENE_KEY);
        if (persisted?.image) {
            ensureTopbarSceneLayer(persisted.image);
            ensureTopbarChip(persisted.tip);
        }
        return false;
    }

    persistTopbarScene({ image: handoff.image, tip: handoff.tip || '' });
    ensureTopbarSceneLayer(handoff.image);

    const topbar = document.querySelector('.app-topbar');
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches || false;
    if (!hasCover || !topbar || reducedMotion) {
        root.classList.remove('has-scene-cover', 'scene-cover-collapsing');
        ensureTopbarChip(handoff.tip);
        return true;
    }

    const rect = topbar.getBoundingClientRect();
    root.style.setProperty('--scene-cover-end', `${Math.max(56, Math.round(rect.height + rect.top))}px`);

    let label = null;
    if (handoff.tip) {
        label = document.createElement('p');
        label.className = 'scene-cover-tip';
        label.textContent = handoff.tip;
        document.body.appendChild(label);
    }

    window.setTimeout(() => {
        root.classList.add('scene-cover-collapsing');
        label?.classList.add('is-collapsing');
        window.setTimeout(() => {
            root.classList.remove('has-scene-cover', 'scene-cover-collapsing');
            label?.remove();
            ensureTopbarChip(handoff.tip);
        }, SCENE_COLLAPSE_MS + 80);
    }, 300);
    return true;
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
    // 登录页交棒的背景收缩开场优先；播过就不再走 cookie 浮层，避免二次打断。
    const sceneEntered = runSceneEntrance();
    if (sceneEntered) {
        clearRevealCookie();
    }
    const wantsReveal = !sceneEntered && shouldRevealFromCookie();
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

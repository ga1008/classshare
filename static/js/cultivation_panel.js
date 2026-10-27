/**
 * 修为面板 — one JSON-driven renderer shared by the classroom page and the
 * profile page. The shell lives in templates/partials/cultivation_panel.html;
 * everything inside the body is built here from
 * GET /api/classrooms/{id}/learning/progress (serialize_student_learning_progress).
 *
 * Ownership: the LQ layer system (via createDomainModal) holds the stack, the
 * focus trap, the scroll lock and the return-focus; this module only renders.
 */
import { apiFetch } from './api.js';
import { showToast } from './ui.js';
import { createDomainModal } from './lq/domain-modal.js';
import { emblemImage, emblemUrl } from './cultivation_emblems.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const STATUS_LABELS = {
    passed: '已破境',
    challenge_ready: '可破境',
    generating: '试炼生成中',
    in_exam: '试炼进行中',
    available: '修炼中',
    locked: '未解锁',
};
const EVENT_TYPE_LABELS = {
    material_progress: '研读',
    submission_graded: '批改',
    task_progress: '任务',
    interaction: '互动',
    consistency: '投入',
    recalibration: '校准',
};

let activePanel = null;

/* ---------------------------------------------------------------- helpers */

function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = String(value);
        else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
        else if (key === 'dataset') Object.assign(node.dataset, value);
        else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of [].concat(children)) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

function svgEl(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
}

const fmt = (value, digits = 1) => {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits).replace(/\.0$/, '') : '0';
};
const clamp01 = (value) => Math.max(0, Math.min(1, Number(value) || 0));
const safeHref = (href) => (typeof href === 'string' && href.startsWith('/') && !href.startsWith('//') ? href : null);

function formatDate(value) {
    if (!value) return '';
    const date = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
    return `${date.getMonth() + 1}/${date.getDate()}`;
}

function ring(percent, { size = 'md', tone = null, label = '' } = {}) {
    const value = Math.round(clamp01(percent / 100) * 100);
    const node = el('span', {
        class: `lq-progress lq-progress--ring lq-cultivation__ring lq-cultivation__ring--${size}`,
        role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': value,
        'aria-label': label || `进度 ${value}%`, 'data-tone': tone,
    });
    const svg = svgEl('svg', { class: 'lq-progress__svg', viewBox: '0 0 36 36', focusable: 'false', 'aria-hidden': 'true' });
    const fill = svgEl('circle', { class: 'lq-progress__fill', cx: 18, cy: 18, r: 15.9155, pathLength: 100, 'stroke-dasharray': `${value} 100` });
    if (value === 0) fill.setAttribute('visibility', 'hidden');
    svg.append(svgEl('circle', { class: 'lq-progress__track', cx: 18, cy: 18, r: 15.9155, pathLength: 100 }), fill);
    node.append(svg, el('span', { class: 'lq-progress__value', 'aria-hidden': 'true', text: `${value}%` }));
    return node;
}

function chip(text, tone = 'neutral') {
    return el('span', { class: 'lq-chip lq-chip--status lq-chip--sm', 'data-lq-component': 'chip', 'data-tone': tone, text });
}

function stageTone(status) {
    switch (status) {
        case 'passed': return 'success';
        case 'challenge_ready': return 'primary';
        case 'generating':
        case 'in_exam': return 'warning';
        case 'locked': return 'neutral';
        default: return 'info';
    }
}

function questionCountFor(stage) {
    const tier = Math.max(1, Math.min(10, Number(stage?.tier) || 10));
    return 6 + Math.round(4 * (tier - 1) / 9);
}

/* ------------------------------------------------------------- sections */

function renderHero(progress, ctx) {
    const level = progress.current_level || {};
    const next = progress.next_stage;
    const eligible = progress.eligible_stage;
    const growth = progress.growth || {};
    const score = Number(progress.score) || 0;
    const theme = level.theme || level.level_key || level.key || 'mortal';

    const levelKey = level.level_key || level.key || 'mortal';
    const markEmblem = Number(level.tier) >= 1 ? emblemImage({ key: levelKey, name: level.level_name || level.name }, { size: 88 }) : null;
    const mark = el('div', { class: `lq-cultivation__mark${markEmblem ? ' has-emblem' : ''}`, 'data-theme': theme, 'aria-hidden': 'true' }, [
        markEmblem || el('span', { text: level.short_name || '未入道' }),
    ]);
    const identity = el('div', { class: 'lq-cultivation__identity' }, [
        el('p', { class: 'lq-cultivation__eyebrow', text: ctx.courseName ? `${ctx.courseName} · 当前境界` : '当前境界' }),
        el('h3', { class: 'lq-cultivation__level', text: level.level_name || level.name || '未入道' }),
        el('p', { class: 'lq-cultivation__aura', text: level.aura_label || level.description || '正在凝聚第一缕灵力' }),
        el('div', { class: 'lq-cultivation__chips' }, [
            growth.phase_label ? chip(growth.phase_label.split(' · ')[0], 'info') : null,
            eligible ? chip(`可破境 · ${eligible.name}`, 'primary') : (next ? chip(`下一境 · ${next.name}`, 'neutral') : chip('十境圆满', 'success')),
            progress.display?.hidden
                ? el('a', { class: 'lq-chip lq-chip--status lq-chip--sm', 'data-lq-component': 'chip', 'data-tone': 'neutral', href: '/profile?section=settings', text: '修为已隐藏 · 设置' })
                : null,
        ]),
    ]);
    const scoreBlock = el('div', { class: 'lq-cultivation__score' }, [
        ring(next ? next.progress_percent : 100, { size: 'lg', tone: 'primary', label: next ? `距 ${next.name} ${next.progress_percent}%` : '全部境界已点亮' }),
        el('div', { class: 'lq-cultivation__score-copy' }, [
            el('strong', { text: fmt(score) }),
            el('span', { text: '修为' }),
            next
                ? el('small', { text: `还差 ${fmt(Math.max(0, Number(next.unlock_score) - score))} 分可挑战 ${next.short_name || next.name}` })
                : el('small', { text: '已达巅峰境界' }),
        ]),
    ]);
    return el('section', { class: 'lq-cultivation__hero', 'data-theme': theme }, [mark, identity, scoreBlock, renderStageAction(progress, ctx)]);
}

function renderStageAction(progress, ctx) {
    const next = progress.next_stage;
    const card = el('div', { class: 'lq-surface lq-cultivation__action', 'data-lq-component': 'surface', 'data-lq-surface': 'embedded' });
    if (!next) {
        card.append(
            el('span', { class: 'lq-cultivation__action-kicker', text: '十境圆满' }),
            el('strong', { text: '所有境界均已破境' }),
            el('p', { text: '继续研读与互助，修为会在日志中持续记录。' }),
        );
        return card;
    }
    const status = next.status;
    card.dataset.status = status;
    card.append(el('span', { class: 'lq-cultivation__action-kicker', text: `下一境 · ${next.name}` }));
    const attempt = next.latest_attempt || {};
    const assignmentId = next.last_exam_assignment_id || attempt.assignment_id;
    if (status === 'challenge_ready') {
        card.append(
            el('strong', { text: `灵力已够，可挑战「${next.certificate_title || next.name}」` }),
            el('p', { text: `AI 会按你的学习记录出一套 ${questionCountFor(next)} 题的个人试炼，${next.pass_score || 80} 分即破境。` }),
            el('div', { class: 'lq-cultivation__action-row' }, [
                el('button', {
                    class: 'lq-btn lq-btn--primary lq-btn--sm', type: 'button', 'data-lq-component': 'button',
                    text: '生成破境试炼', onclick: (event) => ctx.startExam(next.key, event.currentTarget),
                }),
            ]),
        );
        if (attempt?.status === 'failed') {
            card.append(el('p', { class: 'lq-cultivation__action-note', text: `上次试炼 ${fmt(attempt.score)} 分未过线，薄弱点已记入下一次出题，再试一次。` }));
        }
    } else if (status === 'generating') {
        card.append(
            el('strong', { text: 'AI 正在布置你的破境试炼' }),
            el('p', { text: '通常 1-3 分钟完成，完成后会收到通知，也可以稍后刷新本面板。' }),
            el('div', { class: 'lq-cultivation__action-row' }, [
                el('button', { class: 'lq-btn lq-btn--soft lq-btn--sm', type: 'button', 'data-lq-component': 'button', text: '刷新状态', onclick: () => ctx.reload() }),
            ]),
        );
    } else if (status === 'in_exam') {
        const href = assignmentId ? `/exam/take/${assignmentId}` : null;
        card.append(
            el('strong', { text: '破境试炼已就位' }),
            el('p', { text: `完成后由 AI 批改，${next.pass_score || 80} 分及以上即获「${next.certificate_title || next.name}」。` }),
            el('div', { class: 'lq-cultivation__action-row' }, [
                href ? el('a', { class: 'lq-btn lq-btn--primary lq-btn--sm', 'data-lq-component': 'button', href, text: '继续试炼' }) : null,
                el('button', {
                    class: 'lq-btn lq-btn--ghost lq-btn--sm', type: 'button', 'data-lq-component': 'button', text: '删除并重新生成',
                    onclick: (event) => ctx.deleteExam(next.key, event.currentTarget),
                }),
            ]),
        );
    } else {
        const gap = Math.max(0, Number(next.unlock_score) - Number(progress.score || 0));
        const growthNext = progress.growth?.next_stage;
        card.append(
            el('strong', { text: `还差 ${fmt(gap)} 修为解锁试炼` }),
            el('p', { text: growthNext ? `相当于再积累约 ${fmt(growthNext.raw_gap)} 分基础分；最快路径见下方。` : '按下方最快路径继续积累。' }),
        );
    }
    return card;
}

const WIND_ANGLE = 28 * Math.PI / 180;

/**
 * 罡风: streaks blowing from the upper right toward the lower left of the ladder.
 * The selected emblem carries an invisible shield a few pixels outside the art:
 * streaks that reach it are pushed off its surface and slide around it, and each
 * hit leaves a brief arc of light on the shield where it struck.
 * getShield() returns {x, y, r} in canvas pixels (or null) and is read per frame,
 * so scrolling, dragging and re-selecting move the shield with the emblem.
 */
function startWind(canvas, ctx, getShield = () => null) {
    const painter = canvas.getContext('2d');
    if (!painter) return { repaint() {}, stop() {} };
    const dx = -Math.cos(WIND_ANGLE);
    const dy = Math.sin(WIND_ANGLE);
    const SHIELD_REACH = 2.4;      // influence radius, in shield radii
    const MAX_IMPACTS = 40;
    let width = 0;
    let height = 0;
    let particles = [];
    let impacts = [];
    let frame = null;
    let running = false;
    let last = 0;
    let rgb = [148, 163, 184];
    let accent = [99, 102, 241];
    const origin = performance.now();

    const shape = () => ({ len: 16 + Math.random() * 54, speed: 150 + Math.random() * 280, alpha: 0.1 + Math.random() * 0.36, thick: 0.6 + Math.random() * 1.2 });
    const rgbOf = (value, fallback) => {
        const match = String(value || '').match(/\d+(\.\d+)?/g);
        return match && match.length >= 3 ? match.slice(0, 3).map(Number) : fallback;
    };
    const readColor = () => {
        const style = getComputedStyle(canvas);
        rgb = rgbOf(style.color, rgb);
        accent = rgbOf(style.outlineColor, accent);
    };
    // Each streak is drawn along its real recent path, so it bends around the
    // shield instead of pointing a straight tail through it.
    const withVelocity = (p) => {
        p.vx = dx * p.speed;
        p.vy = dy * p.speed;
        p.trail = [{ x: p.x - dx * p.len, y: p.y - dy * p.len }, { x: p.x, y: p.y }];
        return p;
    };
    const spawn = (scatter) => {
        if (scatter) return withVelocity({ x: Math.random() * width * 1.2, y: Math.random() * height, ...shape() });
        const fromTop = Math.random() < width / ((width + height) || 1);
        return withVelocity({
            x: fromTop ? Math.random() * (width + height * 0.8) : width + Math.random() * 60,
            y: fromTop ? -Math.random() * 40 : Math.random() * height * 0.9,
            ...shape(),
        });
    };
    const resize = () => {
        // Layout size, not getBoundingClientRect(): the dialog opens with a scale
        // transform, and a transformed size would misplace every streak.
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        width = canvas.clientWidth;
        height = canvas.clientHeight;
        canvas.width = Math.max(1, Math.round(width * dpr));
        canvas.height = Math.max(1, Math.round(height * dpr));
        painter.setTransform(dpr, 0, 0, dpr, 0, 0);
        readColor();
        const count = Math.min(84, Math.max(18, Math.round(width / 13)));
        particles = Array.from({ length: count }, () => spawn(true));
    };

    /** Bend a particle's velocity around the shield; records an impact on a hit. */
    const deflect = (p, shield, live) => {
        const ox = p.x - shield.x;
        const oy = p.y - shield.y;
        const dist = Math.hypot(ox, oy) || 0.001;
        if (dist > shield.r * SHIELD_REACH) return;
        const nx = ox / dist;
        const ny = oy / dist;
        const inward = p.vx * nx + p.vy * ny; // < 0 when heading into the shield
        let hit = false;
        if (dist < shield.r) {
            // Never inside: sit on the surface and lose the inward motion.
            p.x = shield.x + nx * shield.r;
            p.y = shield.y + ny * shield.r;
            if (inward < 0) { p.vx -= nx * inward * 1.15; p.vy -= ny * inward * 1.15; hit = true; }
        } else if (inward < 0) {
            // The closer to the shield, the more of the inward motion turns sideways.
            const falloff = 0.9 * Math.min(1, ((shield.r * SHIELD_REACH - dist) / (shield.r * (SHIELD_REACH - 1))) ** 1.6);
            p.vx -= nx * inward * falloff;
            p.vy -= ny * inward * falloff;
            const tx = -ny;
            const ty = nx;
            const side = Math.sign(p.vx * tx + p.vy * ty) || 1;
            const boost = -inward * falloff * 0.55;
            p.vx += tx * side * boost;
            p.vy += ty * side * boost;
            if (dist < shield.r * 1.12) hit = true;
        }
        if (live && hit && impacts.length < MAX_IMPACTS && Math.random() < 0.6) {
            impacts.push({ angle: Math.atan2(ny, nx), life: 1, span: 0.22 + Math.random() * 0.3, strength: Math.min(1, -inward / 260) });
        }
    };

    const drawShield = (shield, dt) => {
        if (!shield) { impacts = []; return; }
        const [r, g, b] = accent;
        // Barely-there rim so the shield reads as invisible until struck.
        painter.beginPath();
        painter.arc(shield.x, shield.y, shield.r, 0, Math.PI * 2);
        painter.strokeStyle = `rgba(${r},${g},${b},0.07)`;
        painter.lineWidth = 1;
        painter.stroke();
        impacts = impacts.filter((impact) => (impact.life -= dt * 2.2) > 0);
        for (const impact of impacts) {
            const alpha = impact.life * (0.25 + impact.strength * 0.55);
            painter.beginPath();
            painter.arc(shield.x, shield.y, shield.r + (1 - impact.life) * 3, impact.angle - impact.span, impact.angle + impact.span);
            painter.strokeStyle = `rgba(${r},${g},${b},${alpha})`;
            painter.lineWidth = 1.2 + impact.strength * 1.8;
            painter.lineCap = 'round';
            painter.stroke();
        }
    };

    const paint = (now, advance) => {
        const dt = advance ? Math.min(0.05, (now - last) / 1000 || 0.016) : 0;
        last = now;
        const t = (now - origin) / 1000;
        const gust = 0.7 + 0.45 * Math.sin(t / 1.4) + 0.2 * Math.sin(t / 0.53);
        const shield = getShield();
        painter.clearRect(0, 0, width, height);
        const [r, g, b] = rgb;
        const relax = Math.min(1, dt * 2.4);
        for (const p of particles) {
            // Velocity relaxes back toward the wind once past the shield.
            p.vx += (dx * p.speed * gust - p.vx) * relax;
            p.vy += (dy * p.speed * gust - p.vy) * relax;
            if (shield) deflect(p, shield, advance);
            p.x += p.vx * dt;
            p.y += p.vy * dt;
            if (shield) deflect(p, shield, advance); // keep the integrated position outside
            if (p.x < -p.len || p.y > height + p.len || p.x > width + height + 120 || p.y < -120) Object.assign(p, spawn(false));
            // Append the head and trim the trail to the streak length (stretched by gusts).
            const head = p.trail[p.trail.length - 1];
            if (Math.hypot(p.x - head.x, p.y - head.y) > 3) p.trail.push({ x: p.x, y: p.y });
            else { head.x = p.x; head.y = p.y; }
            const maxLength = p.len * (0.8 + gust * 0.3);
            let length = 0;
            let cut = 0;
            for (let i = p.trail.length - 1; i > 0; i -= 1) {
                length += Math.hypot(p.trail[i].x - p.trail[i - 1].x, p.trail[i].y - p.trail[i - 1].y);
                if (length > maxLength) { cut = i - 1; break; }
            }
            if (cut > 0) p.trail.splice(0, cut);
            const tail = p.trail[0];
            const gradient = painter.createLinearGradient(p.x, p.y, tail.x, tail.y);
            gradient.addColorStop(0, `rgba(${r},${g},${b},${p.alpha})`);
            gradient.addColorStop(1, `rgba(${r},${g},${b},0)`);
            painter.strokeStyle = gradient;
            painter.lineWidth = p.thick;
            painter.lineCap = 'round';
            painter.lineJoin = 'round';
            painter.beginPath();
            painter.moveTo(p.x, p.y);
            for (let i = p.trail.length - 2; i >= 0; i -= 1) painter.lineTo(p.trail[i].x, p.trail[i].y);
            painter.stroke();
        }
        drawShield(shield, dt);
    };
    const loop = (now) => {
        if (!running || !canvas.isConnected) { running = false; frame = null; return; }
        paint(now, true);
        frame = requestAnimationFrame(loop);
    };
    const setRunning = (on) => {
        if (ctx.reducedMotion) { paint(performance.now(), false); return; }
        if (on && !running) { running = true; last = performance.now(); frame = requestAnimationFrame(loop); }
        if (!on && running) { running = false; if (frame) cancelAnimationFrame(frame); frame = null; }
    };
    const resizeObserver = new ResizeObserver(() => { resize(); if (!running) paint(performance.now(), false); });
    resizeObserver.observe(canvas);
    const visibility = new IntersectionObserver((entries) => setRunning(entries.some((entry) => entry.isIntersecting) && !document.hidden));
    visibility.observe(canvas);
    const onVisibility = () => setRunning(!document.hidden && canvas.isConnected);
    document.addEventListener('visibilitychange', onVisibility);
    return {
        repaint: () => { if (!running) paint(performance.now(), false); },
        stop() {
            setRunning(false);
            resizeObserver.disconnect();
            visibility.disconnect();
            document.removeEventListener('visibilitychange', onVisibility);
        },
    };
}

/** Shield centred on the selected step's emblem, a few px outside the art, in canvas coordinates. */
function emblemShield(canvas, item) {
    const art = item?.querySelector('.lq-cultivation__step-emblem, .lq-cultivation__step-origin');
    if (!art) return null;
    const box = canvas.getBoundingClientRect();
    const rect = art.getBoundingClientRect();
    if (!rect.width || !box.width || rect.right < box.left || rect.left > box.right) return null;
    // Screen rects include any transform on the dialog; convert to canvas layout units.
    const scale = canvas.clientWidth / box.width || 1;
    return {
        x: (rect.left + rect.width / 2 - box.left) * scale,
        y: (rect.top + rect.height / 2 - box.top) * scale,
        r: (Math.max(rect.width, rect.height) / 2) * scale + 7,
    };
}

/** Press-and-drag to pan the ladder; a drag never turns into a click. */
function enableDragPan(track) {
    let start = null;
    let dragged = false;
    track.addEventListener('pointerdown', (event) => {
        if (event.button !== 0 || event.pointerType === 'touch') return;
        start = { x: event.clientX, left: track.scrollLeft, id: event.pointerId };
        dragged = false;
    });
    track.addEventListener('pointermove', (event) => {
        if (!start || event.pointerId !== start.id) return;
        const delta = event.clientX - start.x;
        if (!dragged && Math.abs(delta) < 5) return;
        if (!dragged) {
            dragged = true;
            track.classList.add('is-dragging');
            try { track.setPointerCapture(start.id); } catch { /* pointer already released */ }
        }
        track.scrollLeft = start.left - delta;
        event.preventDefault();
    });
    const end = (event) => {
        if (!start || (event && event.pointerId !== start.id)) return;
        try { track.releasePointerCapture(start.id); } catch { /* not captured */ }
        start = null;
        if (dragged) {
            // The click (if any) fires right after pointerup; clear the flag afterwards
            // so a drag that produced no click cannot swallow the next real click.
            window.setTimeout(() => { track.classList.remove('is-dragging'); dragged = false; }, 0);
        }
    };
    track.addEventListener('pointerup', end);
    track.addEventListener('pointercancel', end);
    track.addEventListener('click', (event) => {
        if (!dragged) return;
        dragged = false;
        event.preventDefault();
        event.stopPropagation();
    }, true);
    track.addEventListener('dragstart', (event) => event.preventDefault());
}

function renderPath(progress, ctx) {
    const stages = Array.isArray(progress.stages) ? progress.stages : [];
    const effort = new Map((progress.stage_effort || []).map((row) => [row.key, row]));
    const firstOpen = stages.findIndex((stage) => stage.status !== 'passed');
    const starter = {
        key: 'mortal', name: '未入道', short_name: '起点', tier: 0, unlock_score: 0, theme: 'mortal',
        status: 'passed', description: '正在凝聚第一缕灵力',
    };
    const nodes = [starter, ...stages];
    const standIndex = firstOpen === -1 ? nodes.length - 1 : firstOpen; // last passed step (0 = 起点)
    let selected = firstOpen === -1 ? standIndex : firstOpen + 1;

    const section = el('section', { class: 'lq-cultivation__path', 'aria-labelledby': 'cultivation-path-title' });
    const head = el('div', { class: 'lq-cultivation__section-head' }, [
        el('h3', { id: 'cultivation-path-title', text: '修行天梯' }),
        el('p', { text: '十境逐级攀登，每一境都要通过破境试炼；越高越难。按住拖动可左右浏览。' }),
    ]);
    const track = el('ol', { class: 'lq-cultivation__stairs', tabindex: 0, 'aria-label': '修行天梯，可拖动或用方向键切换境界' });
    const detail = el('div', { class: 'lq-cultivation__stage-detail', 'aria-live': 'polite' });

    const describe = (stage, index) => {
        const effortRow = effort.get(stage.key);
        const parts = [];
        if (stage.tier) parts.push(`第 ${stage.tier} 境`);
        if (Number(stage.unlock_score) > 0) parts.push(`解锁试炼 ${stage.unlock_score} 修为`);
        if (effortRow && Number(effortRow.raw_gap) > 0) parts.push(`较上一境多需约 ${fmt(effortRow.raw_gap)} 基础分`);
        if (stage.certificate?.issued_at) parts.push(`破境于 ${formatDate(stage.certificate.issued_at)}`);
        const statusText = index === standIndex ? '当前所在' : (index === 0 ? '起点' : STATUS_LABELS[stage.status] || '修炼中');
        const statusTone = index === standIndex ? 'primary' : stageTone(stage.status);
        const children = [
            stage.tier ? emblemImage({ key: stage.key, name: stage.name }, { size: 44, className: 'lq-cultivation__detail-emblem' }) : null,
            el('div', { class: 'lq-cultivation__detail-copy' }, [
                el('div', { class: 'lq-cultivation__detail-title' }, [
                    el('strong', { text: `${stage.name}${stage.certificate_title ? ` · ${stage.certificate_title}` : ''}` }),
                    chip(statusText, statusTone),
                ]),
                stage.description ? el('p', { text: stage.description }) : null,
                parts.length ? el('small', { text: parts.join(' · ') }) : null,
            ]),
        ].filter(Boolean);
        if (stage.status === 'passed' && stage.certificate?.id) {
            const viewAward = el('button', {
                type: 'button', class: 'lq-btn lq-btn--glass lq-btn--sm', 'data-lq-component': 'button',
                'data-view-certificate': String(stage.certificate.id), text: '查看道印',
                onclick: async () => {
                    viewAward.disabled = true;
                    try {
                        const { showLearningCertificate } = await import('./learning_certificate_reveal.js');
                        if (!viewAward.isConnected || viewAward.closest('[hidden],[aria-hidden="true"]')) return;
                        const viewer = showLearningCertificate({ ...stage.certificate,
                            course_name: ctx.courseName || stage.certificate.course_name || '课堂',
                            student_name: ctx.userName || stage.certificate.student_name || '',
                        }, { trigger: viewAward });
                        ctx.cleanups.push(() => viewer?.destroy());
                    } catch (error) { showToast(error.message || '道印暂时无法展示，请重试。', 'error'); }
                    finally { viewAward.disabled = false; }
                },
            });
            children.find(child => child.classList.contains('lq-cultivation__detail-copy'))
                ?.append(el('div', { class: 'lq-cultivation__action-row' }, [viewAward]));
        }
        detail.replaceChildren(...children);
    };

    const items = nodes.map((stage, index) => {
        const isStand = index === standIndex;
        const item = el('li', {
            class: `lq-cultivation__step is-${stage.status}${isStand ? ' is-current' : ''}`,
            'data-theme': stage.theme || stage.key,
            'data-index': index,
            style: `--step:${index}`,
            'aria-current': isStand ? 'step' : null,
        });
        const art = stage.tier
            ? emblemImage({ key: stage.key, name: stage.name }, { size: 52, className: 'lq-cultivation__step-emblem' })
            : el('span', { class: 'lq-cultivation__step-origin', 'aria-hidden': 'true' }, [el('b', { text: '凡' })]);
        item.append(
            el('button', {
                class: 'lq-cultivation__step-button', type: 'button', tabindex: -1,
                'aria-label': `${stage.name}，${isStand ? '当前所在' : (STATUS_LABELS[stage.status] || '修炼中')}${stage.unlock_score ? `，解锁 ${stage.unlock_score} 修为` : ''}`,
            }, [
                isStand ? el('span', { class: 'lq-cultivation__here', text: '你在此' }) : null,
                el('span', { class: 'lq-cultivation__step-aura', 'aria-hidden': 'true' }, [art]),
            ]),
            el('span', { class: 'lq-cultivation__tread', 'aria-hidden': 'true' }, [
                el('strong', { text: stage.short_name || stage.name }),
                el('small', { text: index === 0 ? '起点' : (stage.status === 'passed' ? '已破境' : `${stage.unlock_score} 修为`) }),
            ]),
        );
        return item;
    });
    track.append(...items);

    const select = (index, scroll = true) => {
        selected = Math.max(0, Math.min(nodes.length - 1, index));
        items.forEach((item, i) => item.classList.toggle('is-selected', i === selected));
        describe(nodes[selected], selected);
        ctx.onSelect?.();
        if (scroll) items[selected].scrollIntoView({ block: 'nearest', inline: 'center', behavior: ctx.reducedMotion ? 'auto' : 'smooth' });
    };
    track.addEventListener('click', (event) => {
        const step = event.target.closest?.('.lq-cultivation__step');
        if (step && track.contains(step)) select(Number(step.dataset.index));
    });
    track.addEventListener('keydown', (event) => {
        const moves = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
        if (event.key in moves) { select(selected + moves[event.key]); event.preventDefault(); }
        else if (event.key === 'Home') { select(0); event.preventDefault(); }
        else if (event.key === 'End') { select(nodes.length - 1); event.preventDefault(); }
    });
    enableDragPan(track);

    const canvas = el('canvas', { class: 'lq-cultivation__wind', 'aria-hidden': 'true' });
    const sky = el('div', { class: 'lq-cultivation__sky' }, [track, canvas]);
    const prev = el('button', { class: 'lq-btn lq-btn--glass lq-btn--sm lq-btn--icon lq-cultivation__nav', type: 'button', 'data-lq-component': 'button', 'aria-label': '向下一级台阶', text: '‹', onclick: () => select(selected - 1) });
    const nextBtn = el('button', { class: 'lq-btn lq-btn--glass lq-btn--sm lq-btn--icon lq-cultivation__nav', type: 'button', 'data-lq-component': 'button', 'aria-label': '向上一级台阶', text: '›', onclick: () => select(selected + 1) });
    section.append(head, el('div', { class: 'lq-cultivation__viewport' }, [prev, sky, nextBtn]), detail);
    ctx.afterMount(() => {
        select(selected, false);
        items[selected].scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'auto' });
        const wind = startWind(canvas, ctx, () => emblemShield(canvas, items[selected]));
        ctx.cleanups.push(() => wind.stop());
        // Reduced motion paints a static frame; refresh it when the selection or scroll moves.
        if (ctx.reducedMotion) {
            let pending = false;
            const repaintSoon = () => {
                if (pending) return;
                pending = true;
                requestAnimationFrame(() => { pending = false; wind.repaint(); });
            };
            track.addEventListener('scroll', repaintSoon, { passive: true });
            ctx.onSelect = repaintSoon;
        }
    });
    return section;
}

function renderBreakdown(progress) {
    const rows = Array.isArray(progress.breakdown) ? progress.breakdown : [];
    const growth = progress.growth || {};
    const section = el('section', { class: 'lq-cultivation__composition', 'aria-labelledby': 'cultivation-composition-title' });
    section.append(el('div', { class: 'lq-cultivation__section-head' }, [
        el('h3', { id: 'cultivation-composition-title', text: '修为构成' }),
        el('p', { text: growth.raw_score !== undefined
            ? `四项基础积累合计 ${fmt(growth.raw_score)} 分，经成长曲线换算为 ${fmt(growth.score ?? progress.score)} 修为。`
            : (progress.rules?.fairness_note || '') }),
    ]));
    const grid = el('div', { class: 'lq-cultivation__components' });
    rows.forEach((row) => {
        const card = el('article', { class: 'lq-surface lq-cultivation__component', 'data-lq-component': 'surface', 'data-lq-surface': 'embedded', 'data-component': row.key });
        card.append(
            el('div', { class: 'lq-cultivation__component-head' }, [
                ring(row.percent, { size: 'sm', label: `${row.label} ${row.percent}%` }),
                el('div', {}, [el('strong', { text: row.label }), el('span', { text: `${fmt(row.points)} / ${row.weight} 分` })]),
            ]),
            el('p', { class: 'lq-cultivation__component-desc', text: row.description || '' }),
        );
        const list = el('ul', { class: 'lq-cultivation__signals' }, (row.signals || []).map((signal) => el('li', { title: signal.hint || '' }, [
            el('span', { class: 'lq-cultivation__signal-label', text: signal.label }),
            el('span', { class: 'lq-cultivation__signal-bar', 'aria-hidden': 'true' }, [el('i', { style: `--ratio:${clamp01(signal.ratio)}` })]),
            el('span', { class: 'lq-cultivation__signal-value', text: `${signal.value_label || `${signal.percent}%`} · ${fmt(signal.points)}/${fmt(signal.max_points)}` }),
        ])));
        card.append(el('details', { class: 'lq-collapsible lq-cultivation__signal-details', 'data-lq-component': 'collapsible' }, [
            el('summary', { class: 'lq-collapsible__summary' }, [
                el('span', { class: 'lq-collapsible__title', text: `获取方式明细 · 还可得 ${fmt(row.remaining)} 分` }),
                el('span', { class: 'lq-collapsible__indicator', 'aria-hidden': 'true', text: '⌄' }),
            ]),
            el('div', { class: 'lq-collapsible__content' }, [list]),
        ]));
        grid.append(card);
    });
    section.append(grid);
    return section;
}

function renderGrowthNote(progress) {
    const growth = progress.growth;
    if (!growth) return null;
    const stat = (label, value, hint) => el('div', { class: 'lq-cultivation__stat' }, [el('span', { text: label }), el('strong', { text: value }), hint ? el('small', { text: hint }) : null]);
    return el('section', { class: 'lq-surface lq-cultivation__growth', 'data-lq-component': 'surface', 'data-lq-surface': 'embedded' }, [
        el('div', { class: 'lq-cultivation__inline-head' }, [el('h3', { text: '成长曲线' }), growth.phase_label ? el('span', { text: growth.phase_label }) : null]),
        el('div', { class: 'lq-cultivation__stats' }, [
            stat('基础积累', fmt(growth.raw_score), '四项线性合计'),
            stat('曲线加成', `+${fmt(growth.bonus)}`, '前期放大计入'),
            stat('当前增速', `×${fmt(growth.marginal_value, 2)}`, '每 1 基础分换修为'),
        ]),
        el('p', { class: 'lq-cultivation__note', text: growth.note || '' }),
    ]);
}

function renderTrend(progress) {
    const trend = progress.growth_trend;
    const spark = trend?.sparkline;
    const section = el('section', { class: 'lq-surface lq-cultivation__trend', 'data-lq-component': 'surface', 'data-lq-surface': 'embedded' });
    section.append(el('div', { class: 'lq-cultivation__inline-head' }, [
        el('h3', { text: '成长趋势' }),
        trend?.weeks ? el('span', { text: `近 ${trend.weeks} 周 ${trend.total_delta_label || ''}${trend.weekly_delta_label ? ` · 本周 ${trend.weekly_delta_label}` : ''}` }) : null,
    ]));
    if (!spark || !spark.points) {
        section.append(el('p', { class: 'lq-cultivation__empty', text: '每周快照累积两周后，这里会出现你的成长曲线。' }));
        return section;
    }
    const svg = svgEl('svg', { class: 'lq-cultivation__spark', viewBox: spark.view_box || '0 0 220 64', role: 'img', 'aria-label': `修为趋势，最低 ${spark.min}，最高 ${spark.max}` });
    if (spark.area_points) svg.append(svgEl('polygon', { class: 'lq-cultivation__spark-area', points: spark.area_points }));
    svg.append(svgEl('polyline', { class: 'lq-cultivation__spark-line', points: spark.points, fill: 'none' }));
    section.append(svg, el('div', { class: 'lq-cultivation__spark-range' }, [el('span', { text: `低 ${fmt(spark.min)}` }), el('span', { text: `高 ${fmt(spark.max)}` })]));
    return section;
}

function renderSect(progress, ctx) {
    const position = progress.class_position;
    if (!position?.current || !position?.leader || !position?.mountain) return null;
    const leader = position.leader;
    const current = position.current;
    const section = el('section', { class: 'lq-surface lq-cultivation__sect', 'data-lq-component': 'surface', 'data-lq-surface': 'embedded' });
    section.append(el('div', { class: 'lq-cultivation__inline-head' }, [
        el('h3', { text: '宗门实力排行' }),
        el('span', { text: `第 ${current.rank} / ${position.total} 位 · 前 ${current.top_percent}%` }),
    ]));

    const width = 360, height = 150, baseY = 130, peakY = 22, peakX = 178, labelX = 274;
    const minScore = Number(position.mountain.min_score ?? 0);
    const maxScore = Number(position.mountain.max_score ?? 100);
    const range = Math.max(1, maxScore - minScore);
    const currentScore = Number(current.score ?? 0);
    const leaderScore = Number(leader.score ?? maxScore);
    const compact = (text, max) => { const value = String(text || '').trim(); return value.length > max ? `${value.slice(0, max - 1)}…` : value; };
    const yFor = (score) => baseY - clamp01((Number(score) - minScore) / range) * (baseY - peakY);
    const selfY = Math.max(peakY, Math.min(baseY, yFor(currentScore)));
    const selfLabel = leader.is_self ? `你 · ${fmt(currentScore)}` : `${compact(current.name || ctx.userName || '你', 5)} · ${fmt(currentScore)}`;
    const selfLabelY = selfY <= peakY + 12 ? selfY + 18 : selfY - 6;
    const leaderTitle = leader.is_tianjiao ? '本宗天骄' : (leader.anonymous ? '峰顶' : '宗门领先');

    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `宗门实力山峰：${leaderTitle} ${leader.name} 修为 ${fmt(leaderScore)}；虚线为你的修为位置`, class: 'lq-cultivation__mountain-svg' });
    const ridge = `M 30 ${baseY} C 82 112, 116 64, ${peakX} ${peakY} C 238 62, 284 106, 330 ${baseY}`;
    svg.append(
        svgEl('path', { class: 'lq-cultivation__mountain-area', d: `${ridge} Z` }),
        svgEl('path', { class: 'lq-cultivation__mountain-ridge', d: ridge }),
        svgEl('line', { class: 'lq-cultivation__mountain-pole', x1: peakX + 9, x2: peakX + 9, y1: peakY + 4, y2: peakY - 15 }),
        svgEl('path', { class: 'lq-cultivation__mountain-flag', d: `M ${peakX + 9} ${peakY - 15} L ${peakX + 38} ${peakY - 9} L ${peakX + 9} ${peakY - 2} Z` }),
        svgEl('circle', { class: 'lq-cultivation__mountain-peak', cx: peakX, cy: peakY, r: 4.8 }),
    );
    const leaderEmblem = emblemUrl(leader.level?.key || '');
    if (leaderEmblem) {
        svg.append(svgEl('image', { href: leaderEmblem, x: peakX - 34, y: peakY - 20, width: 24, height: 24, class: 'lq-cultivation__mountain-emblem' }));
    }
    const peakText = svgEl('text', { class: 'lq-cultivation__mountain-label', x: peakX, y: peakY + 24, 'text-anchor': 'middle' });
    peakText.textContent = `${leaderTitle} · ${compact(leader.name || '同学', 6)} · ${fmt(leaderScore)}`;
    svg.append(
        peakText,
        svgEl('line', { class: 'lq-cultivation__mountain-self-line', x1: 42, x2: labelX - 7, y1: selfY, y2: selfY }),
        svgEl('circle', { class: 'lq-cultivation__mountain-self-halo', cx: labelX - 8, cy: selfY, r: 8.5 }),
        svgEl('circle', { class: 'lq-cultivation__mountain-self-dot', cx: labelX - 8, cy: selfY, r: 3.5 }),
        svgEl('rect', { class: 'lq-cultivation__mountain-self-bg', x: labelX - 2, y: selfLabelY - 12, width: Math.min(82, Math.max(52, selfLabel.length * 8.2)), height: 17, rx: 8 }),
    );
    const selfText = svgEl('text', { class: 'lq-cultivation__mountain-self-label', x: labelX + 5, y: selfLabelY });
    selfText.textContent = selfLabel;
    svg.append(selfText);
    section.append(svg);

    const rows = Array.isArray(position.ranking) ? position.ranking : [];
    const rankRow = (row, extraClass = '') => {
        const isTianjiao = Boolean(leader.is_tianjiao) && row.rank === 1;
        const classes = `${row.is_self ? 'is-self' : ''} ${extraClass}`.trim();
        return el('li', { class: classes || null, 'data-rank': row.rank }, [
            el('span', { class: 'lq-cultivation__sect-rank', text: String(row.rank) }),
            emblemImage(row.level, { size: 24 }) || el('span', { class: 'lq-cultivation__sect-noemblem', 'aria-hidden': 'true' }),
            el('span', { class: 'lq-cultivation__sect-name' }, [
                el('span', { text: row.is_self ? `${row.name}（你）` : row.name }),
                isTianjiao ? chip('本宗天骄', 'warning') : null,
            ]),
            el('b', { text: fmt(row.score) }),
        ]);
    };
    if (rows.length) {
        const list = el('ol', { class: 'lq-cultivation__sect-list', 'aria-label': '宗门实力榜' }, rows.map((row) => rankRow(row)));
        if (!rows.some((row) => row.is_self)) {
            list.append(rankRow({ rank: current.rank, name: current.name || '你', score: current.score, is_self: true, level: position.current_level }, 'is-gap'));
        }
        section.append(list);
    }
    if (position.self_hidden) {
        section.append(el('p', { class: 'lq-cultivation__hint' }, [
            '你已隐藏修为：同门看不到你的名字与境界。',
            el('a', { href: '/profile?section=settings', text: '修改设置' }),
        ]));
    }
    const othersHidden = Number(position.hidden_count || 0) - (position.self_hidden ? 1 : 0);
    if (othersHidden > 0) section.append(el('p', { class: 'lq-cultivation__hint', text: `另有 ${othersHidden} 位同门隐世修炼，未列入榜单。` }));
    section.append(el('p', { class: 'lq-cultivation__hint', text: leader.is_self && leader.is_tianjiao
        ? `你就是本宗天骄，修为 ${fmt(currentScore)}。`
        : `你超过了 ${current.surpass_percent ?? 0}% 的同门。` }));
    return section;
}

function renderOpportunities(progress) {
    const items = Array.isArray(progress.score_opportunities) ? progress.score_opportunities : [];
    const section = el('section', { class: 'lq-surface lq-cultivation__opportunities', 'data-lq-component': 'surface', 'data-lq-surface': 'embedded' });
    section.append(el('div', { class: 'lq-cultivation__inline-head' }, [el('h3', { text: '最快路径' }), el('span', { text: '按预计增量排序' })]));
    if (!items.length) {
        const next = progress.next_stage;
        const busy = next && ['challenge_ready', 'generating', 'in_exam'].includes(next.status);
        section.append(el('p', { class: 'lq-cultivation__empty', text: busy ? '灵力已够，先去完成破境试炼。' : '当前范围内已无可增项，继续保持投入。' }));
        return section;
    }
    section.append(el('ul', { class: 'lq-cultivation__opportunity-list' }, items.map((item) => {
        const href = safeHref(item.action_url);
        return el('li', {}, [el(href ? 'a' : 'div', { class: 'lq-cultivation__opportunity', href, 'data-type': item.type || '' }, [
            el('div', {}, [el('strong', { text: item.title || '' }), item.description ? el('small', { text: item.description }) : null]),
            el('b', { text: `+${fmt(item.estimated_delta)}` }),
        ])]);
    })));
    return section;
}

function renderLedger(progress) {
    const events = Array.isArray(progress.recent_score_events) ? progress.recent_score_events : [];
    const details = el('details', { class: 'lq-collapsible lq-cultivation__ledger', 'data-lq-component': 'collapsible' });
    details.append(el('summary', { class: 'lq-collapsible__summary' }, [
        el('span', { class: 'lq-collapsible__title', text: `修为日志 · 最近 ${events.length} 条` }),
        el('span', { class: 'lq-collapsible__indicator', 'aria-hidden': 'true', text: '⌄' }),
    ]));
    const body = el('div', { class: 'lq-collapsible__content' });
    if (!events.length) {
        body.append(el('p', { class: 'lq-cultivation__empty', text: '完成一次研读、提交或互动后，修为变化会记录在这里。' }));
    } else {
        body.append(el('ul', { class: 'lq-cultivation__ledger-list' }, events.map((event) => {
            const delta = Number(event.delta) || 0;
            return el('li', {}, [
                el('span', { class: 'lq-cultivation__ledger-when', text: formatDate(event.created_at) }),
                el('span', { class: 'lq-cultivation__ledger-what', text: `${event.component_label || EVENT_TYPE_LABELS[event.event_type] || ''}${event.title ? ` · ${event.title}` : ''}` }),
                el('strong', { class: delta >= 0 ? 'is-positive' : 'is-negative', text: event.delta_label || `${delta >= 0 ? '+' : ''}${fmt(delta)}` }),
            ]);
        })));
    }
    details.append(body);
    return details;
}

/* ---------------------------------------------------------------- panel */

function createContext(panel, options) {
    const mounted = [];
    return {
        reducedMotion: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false,
        courseName: options.courseName || '',
        userName: options.userName || '',
        cleanups: panel.cleanups,
        afterMount: (callback) => mounted.push(callback),
        runMounted: () => { while (mounted.length) mounted.shift()?.(); },
        reload: () => panel.reload(),
        startExam: (stageKey, button) => panel.startExam(stageKey, button),
        deleteExam: (stageKey, button) => panel.deleteExam(stageKey, button),
    };
}

function renderBody(body, progress, ctx) {
    body.textContent = '';
    body.removeAttribute('aria-busy');
    const main = el('div', { class: 'lq-cultivation__main' }, [renderBreakdown(progress), renderGrowthNote(progress)]);
    const aside = el('div', { class: 'lq-cultivation__aside' }, [renderSect(progress, ctx), renderTrend(progress), renderOpportunities(progress), renderLedger(progress)]);
    body.append(renderHero(progress, ctx), renderPath(progress, ctx), el('div', { class: 'lq-cultivation__grid' }, [main, aside]));
    ctx.runMounted();
}

function renderError(body, message) {
    body.textContent = '';
    body.removeAttribute('aria-busy');
    body.append(el('div', { class: 'lq-empty lq-cultivation__error', role: 'alert' }, [
        el('p', { class: 'lq-empty__copy', text: message || '修为暂时无法读取，请稍后重试。' }),
    ]));
}

function createPanel(root) {
    const body = root.querySelector('[data-cultivation-panel-body]');
    const foot = root.querySelector('[data-cultivation-panel-foot]');
    const title = root.querySelector('[data-cultivation-panel-title]');
    const kicker = root.querySelector('[data-cultivation-panel-kicker]');
    const closeBtn = root.querySelector('[data-cultivation-panel-close]');
    const surface = root.querySelector('.lq-dialog__surface');
    let current = { classOfferingId: null, options: {}, trigger: null };
    let owner = null;
    let requestSerial = 0;
    const preloadedFor = new Set();

    const panel = {
        root,
        isOpen: () => !root.hidden,
        async open(options = {}) {
            current = { classOfferingId: options.classOfferingId, options, trigger: options.trigger || document.activeElement };
            if (title) title.textContent = options.title || '修为';
            if (kicker) kicker.textContent = options.courseName ? `${options.courseName} · Cultivation Path` : 'Cultivation Path';
            foot.textContent = '';
            const href = safeHref(options.classroomHref);
            if (href) foot.append(el('a', { class: 'lq-btn lq-btn--primary', 'data-lq-component': 'button', href, text: '前往课堂' }));
            foot.append(el('button', { class: 'lq-btn lq-btn--ghost', type: 'button', 'data-lq-component': 'button', text: '关闭', onclick: () => panel.close('button') }));
            body.textContent = '';
            body.setAttribute('aria-busy', 'true');
            body.append(el('p', { class: 'lq-cultivation__loading', text: '正在读取修为…' }));
            owner ??= createDomainModal(root, {
                type: 'modal', surface, initialFocus: closeBtn,
                onClose: () => { panel.teardown(); root.hidden = true; current.trigger?.setAttribute?.('aria-expanded', 'false'); },
            });
            root.hidden = false;
            current.trigger?.setAttribute?.('aria-expanded', 'true');
            owner.open({ trigger: current.trigger });
            const preloadKey = String(options.classOfferingId);
            if (options.progress && !preloadedFor.has(preloadKey)) {
                preloadedFor.add(preloadKey); // first open shows page data; later opens re-fetch
                panel.render(options.progress);
            } else {
                await panel.reload();
            }
        },
        close(reason = 'programmatic') {
            return owner ? owner.close(reason) : Promise.resolve(false);
        },
        cleanups: [],
        teardown() {
            while (panel.cleanups.length) {
                try { panel.cleanups.pop()?.(); } catch { /* best effort */ }
            }
        },
        render(progress) {
            panel.teardown();
            try {
                renderBody(body, progress, createContext(panel, current.options));
            } catch (error) {
                console.error('[cultivation-panel] render failed', error);
                renderError(body, '修为面板渲染失败，请刷新页面重试。');
            }
        },
        async reload() {
            if (!current.classOfferingId) { renderError(body, '缺少课堂信息，无法读取修为。'); return; }
            const serial = ++requestSerial;
            const classOfferingId = current.classOfferingId;
            try {
                const result = await apiFetch(`/api/classrooms/${classOfferingId}/learning/progress`, { silent: true });
                if (serial !== requestSerial || root.hidden) return; // superseded by a newer open/reload
                if (!result?.progress) throw new Error(result?.message || '修为暂时无法读取');
                current.options.onProgress?.(result.progress);
                panel.render(result.progress);
            } catch (error) {
                if (serial !== requestSerial) return;
                renderError(body, error.message || '修为暂时无法读取，请稍后重试。');
            }
        },
        async startExam(stageKey, button) {
            if (!current.classOfferingId || !stageKey) return;
            const original = button.textContent;
            button.disabled = true; button.classList.add('is-loading'); button.textContent = 'AI 正在布置试炼…';
            try {
                const result = await apiFetch(`/api/classrooms/${current.classOfferingId}/learning/stages/${stageKey}/exam`, { method: 'POST', silent: true });
                if (result.status === 'generating') {
                    showToast(result.message || 'AI 正在生成破境试炼，完成后会通知你。', 'info');
                    await panel.reload();
                    return;
                }
                showToast(result.status === 'exists' ? '破境试炼已准备好，正在进入。' : '破境试炼已生成。', 'success');
                window.location.href = safeHref(result.exam_url) || `/classroom/${current.classOfferingId}`;
            } catch (error) {
                showToast(error.message || '破境试炼生成失败', 'error');
                button.disabled = false; button.classList.remove('is-loading'); button.textContent = original;
            }
        },
        async deleteExam(stageKey, button) {
            if (!current.classOfferingId || !stageKey) return;
            if (!window.confirm('确定删除这份个人破境试炼吗？删除后可以重新生成。')) return;
            button.disabled = true;
            try {
                const result = await apiFetch(`/api/classrooms/${current.classOfferingId}/learning/stages/${stageKey}/exam`, { method: 'DELETE', silent: true });
                showToast(result.message || '个人破境试炼已删除，可以重新试炼。', 'success');
                await panel.reload();
            } catch (error) {
                showToast(error.message || '删除试炼失败', 'error');
                button.disabled = false;
            }
        },
    };
    closeBtn?.addEventListener('click', () => panel.close('button'));
    return panel;
}

export function getCultivationPanel(doc = document) {
    if (activePanel && doc.contains(activePanel.root)) return activePanel;
    const root = doc.querySelector('[data-cultivation-panel]');
    if (!root) return null;
    activePanel = createPanel(root);
    return activePanel;
}

/**
 * Open the panel.
 * options: { classOfferingId, progress?, trigger?, classroomHref?, courseName?, userName?, title?, onProgress? }
 */
export async function openCultivationPanel(options = {}) {
    const panel = getCultivationPanel();
    if (!panel) return null;
    await panel.open(options);
    return panel;
}

/**
 * Bind every `[data-cultivation-panel-open]` trigger inside `scope`. A trigger
 * carries data-class-offering-id and may carry data-classroom-href /
 * data-course-name / data-panel-title. `defaults` supplies the page's own
 * classOfferingId, preloaded progress, userName and an onProgress hook.
 */
export function initCultivationPanel(defaults = {}, scope = document) {
    const panel = getCultivationPanel(scope.ownerDocument || scope);
    if (!panel) return null;
    Array.from(scope.querySelectorAll('[data-cultivation-panel-open]')).forEach((trigger) => {
        if (trigger.dataset.cultivationPanelBound) return;
        trigger.dataset.cultivationPanelBound = '1';
        trigger.setAttribute('aria-haspopup', 'dialog');
        trigger.setAttribute('aria-controls', panel.root.id);
        trigger.setAttribute('aria-expanded', 'false');
        trigger.addEventListener('click', (event) => {
            const classOfferingId = Number(trigger.dataset.classOfferingId || defaults.classOfferingId);
            if (!classOfferingId) return;
            event.preventDefault();
            const sameClassroom = Number(defaults.classOfferingId) === classOfferingId;
            openCultivationPanel({
                classOfferingId,
                trigger,
                progress: sameClassroom ? defaults.progress : undefined,
                classroomHref: trigger.dataset.classroomHref || (sameClassroom ? null : `/classroom/${classOfferingId}`),
                courseName: trigger.dataset.courseName || defaults.courseName || '',
                userName: defaults.userName || '',
                title: trigger.dataset.panelTitle || defaults.title || '修为',
                onProgress: (progress) => {
                    if (sameClassroom) defaults.progress = progress;
                    defaults.onProgress?.(progress, classOfferingId);
                },
            });
        });
    });
    return panel;
}

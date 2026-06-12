import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/ui.js';

const REVEAL_DELAY_MS = 560;
const TRANSITION_MS = 280;

const THEME_COLORS = {
    qi_awakening: ['#14b8a6', '#facc15'],
    qi_refining: ['#22c55e', '#67e8f9'],
    foundation: ['#0ea5e9', '#f59e0b'],
    application_seed: ['#6366f1', '#22c55e'],
    golden_core: ['#f59e0b', '#ef4444'],
    practical_mastery: ['#f97316', '#14b8a6'],
    systems_thinking: ['#8b5cf6', '#38bdf8'],
    nascent_soul: ['#38bdf8', '#a78bfa'],
    independent_path: ['#10b981', '#f472b6'],
    mentor_heart: ['#0f766e', '#fbbf24'],
    self_directed: ['#10b981', '#f472b6'],
    teacher_guiding: ['#0f766e', '#fbbf24'],
    default: ['#14b8a6', '#f59e0b'],
};

function storageGet(key) {
    try {
        return window.localStorage?.getItem(key) || '';
    } catch (_) {
        return '';
    }
}

function storageSet(key, value) {
    try {
        window.localStorage?.setItem(key, value);
    } catch (_) {
        // Browser privacy settings can disable localStorage; server reveal state still handles replay.
    }
}

function selectCertificate(config) {
    return config?.learningProgress?.latest_unrevealed_certificate
        || config?.cultivationProfile?.latest_unrevealed_certificate
        || config?.learningCertificate
        || config?.latest_unrevealed_certificate
        || null;
}

function certificatePayload(card, certificate = {}) {
    return {
        id: String(card?.dataset.certificateId || certificate.id || ''),
        title: String(card?.dataset.certificateTitle || certificate.title || '破境道印'),
        level: String(card?.dataset.certificateLevel || certificate.level_name || certificate.name || '新境界'),
        course: String(card?.dataset.certificateCourse || certificate.course_name || '课堂'),
        student: String(card?.dataset.certificateStudent || certificate.student_name || ''),
        code: String(card?.dataset.certificateCode || certificate.certificate_code || ''),
        issued: String(card?.dataset.certificateIssued || certificate.issued_at || ''),
        theme: String(card?.dataset.theme || certificate.theme || certificate.level_key || certificate.stage_key || 'default'),
    };
}

function markCertificateRevealed(certificateId, backdrop) {
    if (!certificateId || backdrop?.dataset.revealMarked === 'true') return;
    if (backdrop) backdrop.dataset.revealMarked = 'true';
    apiFetch(`/api/learning/certificates/${encodeURIComponent(certificateId)}/revealed`, {
        method: 'POST',
        silent: true,
    }).catch((error) => {
        console.debug('learning certificate reveal mark failed', error);
        if (backdrop) backdrop.dataset.revealMarked = 'false';
    });
}

function roundedRect(ctx, x, y, width, height, radius) {
    const safeRadius = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + safeRadius, y);
    ctx.arcTo(x + width, y, x + width, y + height, safeRadius);
    ctx.arcTo(x + width, y + height, x, y + height, safeRadius);
    ctx.arcTo(x, y + height, x, y, safeRadius);
    ctx.arcTo(x, y, x + width, y, safeRadius);
    ctx.closePath();
}

function wrapCanvasText(ctx, text, maxWidth, maxLines = 2) {
    const chars = Array.from(String(text || '').trim());
    const lines = [];
    let current = '';
    chars.forEach((char) => {
        const next = `${current}${char}`;
        if (ctx.measureText(next).width <= maxWidth || !current) {
            current = next;
            return;
        }
        lines.push(current);
        current = char;
    });
    if (current) lines.push(current);
    if (lines.length <= maxLines) return lines;
    const clipped = lines.slice(0, maxLines);
    let last = clipped[clipped.length - 1];
    while (last.length > 1 && ctx.measureText(`${last}...`).width > maxWidth) {
        last = last.slice(0, -1);
    }
    clipped[clipped.length - 1] = `${last}...`;
    return clipped;
}

function formatIssuedDate(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return raw.slice(0, 16);
    return new Intl.DateTimeFormat('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
    }).format(date);
}

function drawCertificateCanvas(ctx, payload, width, height) {
    const [primary, accent] = THEME_COLORS[payload.theme] || THEME_COLORS.default;
    const bg = ctx.createLinearGradient(0, 0, width, height);
    bg.addColorStop(0, '#fffdf4');
    bg.addColorStop(0.52, '#f8fffb');
    bg.addColorStop(1, '#eef7ff');
    ctx.fillStyle = bg;
    roundedRect(ctx, 0, 0, width, height, 42);
    ctx.fill();

    const glow = ctx.createRadialGradient(180, 120, 20, 180, 120, 420);
    glow.addColorStop(0, `${primary}55`);
    glow.addColorStop(1, `${primary}00`);
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, width, height);

    const cornerGlow = ctx.createRadialGradient(width - 150, height - 120, 20, width - 150, height - 120, 380);
    cornerGlow.addColorStop(0, `${accent}50`);
    cornerGlow.addColorStop(1, `${accent}00`);
    ctx.fillStyle = cornerGlow;
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    ctx.globalAlpha = 0.18;
    ctx.strokeStyle = primary;
    ctx.lineWidth = 2;
    for (let x = -height; x < width; x += 72) {
        ctx.beginPath();
        ctx.moveTo(x, height + 20);
        ctx.lineTo(x + height, 0);
        ctx.stroke();
    }
    ctx.globalAlpha = 0.14;
    ctx.strokeStyle = accent;
    for (let y = 72; y < height; y += 86) {
        ctx.beginPath();
        ctx.moveTo(70, y);
        ctx.bezierCurveTo(width * 0.34, y - 38, width * 0.62, y + 38, width - 70, y);
        ctx.stroke();
    }
    ctx.restore();

    ctx.save();
    ctx.lineWidth = 3;
    ctx.strokeStyle = `${primary}88`;
    roundedRect(ctx, 50, 50, width - 100, height - 100, 34);
    ctx.stroke();
    ctx.strokeStyle = `${accent}77`;
    roundedRect(ctx, 78, 78, width - 156, height - 156, 28);
    ctx.stroke();
    ctx.restore();

    const sealX = width / 2;
    const sealY = 158;
    ctx.save();
    const sealGradient = ctx.createLinearGradient(sealX - 95, sealY - 95, sealX + 95, sealY + 95);
    sealGradient.addColorStop(0, primary);
    sealGradient.addColorStop(1, accent);
    ctx.fillStyle = sealGradient;
    ctx.beginPath();
    ctx.arc(sealX, sealY, 82, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.72)';
    ctx.lineWidth = 6;
    ctx.stroke();
    ctx.fillStyle = '#ffffff';
    ctx.font = '900 42px "Microsoft YaHei", "PingFang SC", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText((payload.level || '道').slice(0, 2), sealX, sealY);
    ctx.restore();

    ctx.textAlign = 'center';
    ctx.fillStyle = primary;
    ctx.font = '900 30px "Microsoft YaHei", "PingFang SC", sans-serif';
    ctx.fillText('破境道印', width / 2, 292);

    ctx.fillStyle = '#0f172a';
    ctx.font = '900 58px "Microsoft YaHei", "PingFang SC", sans-serif';
    const titleLines = wrapCanvasText(ctx, payload.title, width - 240, 2);
    titleLines.forEach((line, index) => {
        ctx.fillText(line, width / 2, 362 + index * 62);
    });

    const levelY = titleLines.length > 1 ? 506 : 456;
    ctx.fillStyle = primary;
    ctx.font = '900 70px "Microsoft YaHei", "PingFang SC", sans-serif';
    ctx.fillText(payload.level, width / 2, levelY);

    ctx.fillStyle = '#475569';
    ctx.font = '700 30px "Microsoft YaHei", "PingFang SC", sans-serif';
    const courseLine = [payload.course, payload.student].filter(Boolean).join(' · ');
    wrapCanvasText(ctx, courseLine, width - 260, 2).forEach((line, index) => {
        ctx.fillText(line, width / 2, levelY + 70 + index * 38);
    });

    ctx.fillStyle = '#64748b';
    ctx.font = '700 24px "Microsoft YaHei", "PingFang SC", sans-serif';
    const issued = formatIssuedDate(payload.issued);
    const footer = [payload.code, issued].filter(Boolean).join(' · ');
    ctx.fillText(footer || 'LanShare Cultivation Certificate', width / 2, height - 118);

    ctx.fillStyle = `${accent}22`;
    roundedRect(ctx, width / 2 - 250, height - 92, 500, 30, 15);
    ctx.fill();
}

async function downloadCertificateImage(card, certificate) {
    const payload = certificatePayload(card, certificate);
    const canvas = document.createElement('canvas');
    const width = 1200;
    const height = 820;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('当前浏览器暂不支持证书图片生成。');
    ctx.scale(dpr, dpr);
    if (document.fonts?.ready) {
        await document.fonts.ready;
    }
    drawCertificateCanvas(ctx, payload, width, height);

    const fileName = `${payload.level || 'learning-certificate'}-${payload.code || payload.id || Date.now()}`
        .replace(/[\\/:*?"<>|\s]+/g, '-')
        .replace(/-+/g, '-')
        .slice(0, 90);
    const dataUrl = canvas.toDataURL('image/png');
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png', 0.96));
    if (blob) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${fileName}.png`;
        link.rel = 'noopener';
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1500);
        return;
    }
    window.open(dataUrl, '_blank', 'noopener');
}

export function initLearningCertificateReveal(config = window.APP_CONFIG || {}) {
    const backdrop = document.getElementById('learning-certificate-backdrop');
    const card = backdrop?.querySelector('[data-learning-certificate-card]');
    if (!backdrop || !card) return;

    const certificate = selectCertificate(config);
    const payload = certificatePayload(card, certificate);
    if (!payload.id) return;

    const closeBtn = document.getElementById('learning-certificate-close');
    const saveBtn = document.getElementById('learning-certificate-save');
    const storageKey = `learning-cert-seen:${payload.id}`;
    const hasLocalReveal = storageGet(storageKey) === '1';
    const close = () => {
        backdrop.classList.remove('is-open');
        backdrop.setAttribute('aria-hidden', 'true');
        storageSet(storageKey, '1');
        window.setTimeout(() => {
            backdrop.hidden = true;
            document.body.classList.remove('has-learning-certificate');
        }, TRANSITION_MS);
    };

    saveBtn?.addEventListener('click', async () => {
        const originalText = saveBtn.textContent;
        saveBtn.disabled = true;
        saveBtn.textContent = '生成中...';
        try {
            await downloadCertificateImage(card, certificate);
            showToast('道印图片已生成。移动端若未自动下载，可在新打开的图片中长按保存。', 'success', 4200);
        } catch (error) {
            showToast(error.message || '道印图片生成失败。', 'error');
        } finally {
            saveBtn.disabled = false;
            saveBtn.textContent = originalText;
        }
    });

    closeBtn?.addEventListener('click', close);
    document.addEventListener('keydown', (event) => {
        if (!backdrop.hidden && event.key === 'Escape') close();
    });

    if (hasLocalReveal) {
        markCertificateRevealed(payload.id, backdrop);
        backdrop.hidden = true;
        backdrop.setAttribute('aria-hidden', 'true');
        return;
    }

    window.setTimeout(() => {
        backdrop.hidden = false;
        backdrop.setAttribute('aria-hidden', 'false');
        document.body.classList.add('has-learning-certificate');
        markCertificateRevealed(payload.id, backdrop);
        window.requestAnimationFrame(() => {
            backdrop.classList.add('is-open');
            closeBtn?.focus?.({ preventScroll: true });
        });
    }, REVEAL_DELAY_MS);
}

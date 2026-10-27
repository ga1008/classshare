/** One artwork identity for the reveal and its transparent PNG export. */
const STAGES = [
    ['qi_awakening', '#38bdf8', '#d7f3ff'], ['qi_refining', '#2dd4bf', '#d6fff7'],
    ['foundation', '#22d3ee', '#e0fcff'], ['application_seed', '#34d399', '#ddffed'],
    ['golden_core', '#fbbf24', '#fff0ba'], ['practical_mastery', '#fb923c', '#ffe3b9'],
    ['systems_thinking', '#818cf8', '#e2e7ff'], ['nascent_soul', '#c084fc', '#f4e0ff'],
    ['independent_path', '#fb7185', '#ffe0ce'], ['mentor_heart', '#f9cf70', '#fff2ce'],
];
const ALIASES = { self_directed: 'independent_path', teacher_guiding: 'mentor_heart' };

export function awardStyle(value) {
    const key = ALIASES[value] || value;
    const index = Math.max(0, STAGES.findIndex(stage => stage[0] === key));
    const [name, primary, light] = STAGES[index];
    return { key: name, tier: index + 1, primary, light,
        url: `/static/img/cultivation/awards/${name}.svg?v=20261027`,
        particles: 6 + index * 2, duration: 1900 + index * 360 };
}

export function certificateFilename(payload) {
    const name = [payload.level || '破境道印', payload.code || payload.id].filter(Boolean).join('-')
        .replace(/[\\/:*?"<>|\s\u0000-\u001f]+/g, '-').replace(/-+/g, '-').slice(0, 110);
    return `${name || '破境道印'}.png`;
}

export function wrapCertificateText(ctx, value, width, maxLines = 2) {
    const lines = []; let line = '';
    for (const char of String(value || '').trim()) {
        if (line && ctx.measureText(line + char).width > width) { lines.push(line); line = ''; }
        line += char;
    }
    if (line) lines.push(line);
    if (lines.length <= maxLines) return lines;
    const clipped = lines.slice(0, maxLines);
    let last = clipped[maxLines - 1];
    while (last && ctx.measureText(`${last}…`).width > width) last = Array.from(last).slice(0, -1).join('');
    clipped[maxLines - 1] = `${last}…`;
    return clipped;
}

export async function createCertificateImage(payload, doc = document) {
    const style = awardStyle(payload.theme), canvas = doc.createElement('canvas');
    // Fixed export dimensions are independent of screen size and device density.
    canvas.width = 1600; canvas.height = 1850;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('当前浏览器暂不支持道印图片生成。');
    const art = new Image(); art.decoding = 'async'; art.src = style.url;
    await art.decode();
    if (doc.fonts?.ready) await doc.fonts.ready;
    ctx.drawImage(art, 0, 0, 1600, 1600);
    // A small enamel inscription is part of the exported artwork, not UI chrome.
    ctx.fillStyle = '#0b1422';
    ctx.beginPath(); ctx.roundRect(170, 1490, 1260, 300, 90); ctx.fill();
    ctx.strokeStyle = style.primary; ctx.lineWidth = 2; ctx.stroke();
    ctx.textAlign = 'center'; ctx.fillStyle = style.light;
    ctx.font = '600 42px "Microsoft YaHei", "PingFang SC", sans-serif';
    const lines = wrapCertificateText(ctx, [payload.course, payload.student].filter(Boolean).join(' · '), 1120, 2);
    lines.forEach((line, index) => ctx.fillText(line, 800, 1565 + index * 55));
    ctx.font = '26px "Microsoft YaHei", "PingFang SC", sans-serif';
    ctx.fillText(payload.title || payload.level || '破境道印', 800, 1690);
    ctx.font = '22px "Microsoft YaHei", "PingFang SC", sans-serif';
    const issued = String(payload.issued || '').slice(0, 10);
    wrapCertificateText(ctx, [payload.code, issued].filter(Boolean).join(' · '), 1120, 2)
        .forEach((line, index) => ctx.fillText(line, 800, 1735 + index * 29));
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('道印图片生成失败。')), 'image/png'));
}

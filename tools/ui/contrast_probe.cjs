'use strict';

const { runCli } = require('./audit_runtime.cjs');

async function inspectContrast(page, options = {}) {
  const result = await page.evaluate(() => {
    const samples = [];
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    function color(value) {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = value;
      context.fillRect(0, 0, 1, 1);
      const pixel = context.getImageData(0, 0, 1, 1).data;
      return [pixel[0], pixel[1], pixel[2], pixel[3] / 255];
    }
    function over(front, back) {
      const alpha = front[3] + back[3] * (1 - front[3]);
      return [0, 1, 2].map((i) => alpha ? (front[i] * front[3] + back[i] * back[3] * (1 - front[3])) / alpha : 0).concat(alpha);
    }
    function luminance(rgba) {
      const linear = rgba.slice(0, 3).map((value) => { const channel = value / 255; return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4; });
      return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    }
    function hidden(element) {
      for (let node = element; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) || Number(style.opacity) === 0 || style.contentVisibility === 'hidden') return true;
      }
      return false;
    }
    function background(element) {
      const stack = [];
      const reasons = new Set();
      let opaque = false;
      for (let node = element; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        // Group opacity/filter/blending may affect the final foreground even
        // after an opaque background has stopped background accumulation.
        if (Number(style.opacity) !== 1) reasons.add('group-opacity');
        if (style.filter !== 'none') reasons.add('filter');
        if (style.mixBlendMode !== 'normal') reasons.add('blend-mode');
        for (const pseudo of ['::before', '::after']) {
          const ps = getComputedStyle(node, pseudo);
          if (!['none', 'normal'].includes(ps.content) && ps.display !== 'none' && ps.visibility === 'visible' && Number(ps.opacity) > 0
            && (ps.backgroundImage !== 'none' || color(ps.backgroundColor)[3] > 0 || (ps.backdropFilter && ps.backdropFilter !== 'none'))) reasons.add('painted-pseudo-element');
        }
        if (opaque) continue;
        if (style.backgroundImage !== 'none') reasons.add('image-or-gradient-background');
        const backdrop = style.backdropFilter || style.webkitBackdropFilter || 'none';
        if (backdrop !== 'none') reasons.add('backdrop-filter-needs-pixel-probe');
        const bg = color(style.backgroundColor);
        stack.push(bg);
        if (bg[3] === 1) opaque = true;
      }
      let bg = [255, 255, 255, 1];
      for (const layer of stack.reverse()) bg = over(layer, bg);
      return { color: bg, reasons: [...reasons], canvasAssumption: !opaque ? 'browser-default-white' : null };
    }
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const text = walker.currentNode;
      if (!text.textContent.trim()) continue;
      const element = text.parentElement;
      if (!element || element.closest('script, style, noscript, template') || hidden(element)) continue;
      const range = document.createRange();
      range.selectNodeContents(text);
      if (![...range.getClientRects()].some((r) => r.width && r.height && r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight)) continue;
      const style = getComputedStyle(element);
      const bg = background(element);
      const foreground = over(color(style.color), bg.color);
      if (style.webkitTextFillColor && style.webkitTextFillColor !== style.color) bg.reasons.push('text-fill-color');
      if (style.backgroundClip === 'text' || style.webkitBackgroundClip === 'text') bg.reasons.push('background-clipped-to-text');
      const fontSize = Number.parseFloat(style.fontSize);
      const largeText = fontSize >= 24 || (fontSize >= 18.6667 && Number.parseInt(style.fontWeight, 10) >= 700);
      const threshold = largeText ? 3 : 4.5;
      const fgL = luminance(foreground), bgL = luminance(bg.color);
      const ratio = (Math.max(fgL, bgL) + 0.05) / (Math.min(fgL, bgL) + 0.05);
      samples.push({
        host: element.id ? `#${element.id}` : `${element.localName}${[...element.classList].slice(0, 3).map((c) => `.${c}`).join('')}`,
        text: text.textContent.trim().slice(0, 100), fontSize, fontWeight: style.fontWeight, largeText, threshold,
        foreground: foreground.slice(0, 3), background: bg.color.slice(0, 3), canvasAssumption: bg.canvasAssumption,
        ratio: bg.reasons.length ? null : Number(ratio.toFixed(3)),
        status: bg.reasons.length ? 'unmeasured' : ratio >= threshold ? 'passed' : 'failed',
        reasons: bg.reasons,
      });
    }
    return { samples, limitations: ['Computed sRGB contrast of visible direct text nodes only; form values/placeholders, icons, pseudo text and SVG text require additional probes.', 'Images, gradients, backdrop filters, painted pseudo-elements and compositing effects require screenshot/pixel sampling before acceptance.', 'Computed colors do not measure font antialiasing or text occlusion by unrelated painted elements.'] };
  });
  const failed = result.samples.filter((sample) => sample.status === 'failed').length;
  const unmeasured = result.samples.filter((sample) => sample.status === 'unmeasured').length;
  return { ...result, sampledTextCount: result.samples.length, failedCount: failed, unmeasuredCount: unmeasured, enforced: !!options.enforce,
    status: !result.samples.length ? 'unmeasured' : failed ? 'failed' : unmeasured ? 'needs-pixel-probe' : 'passed' };
}

/** Conservative pixel proof for explicitly marked plain-text specimens.
 * Hide glyph paint only, retaining actual browser backgrounds/blur/gradients.
 * Compare the intended full foreground against every pixel in each line box.
 * This deliberately does not score antialiased glyph-edge pixels as text.
 */
async function inspectTextPixels(page, { selector = '[data-lq-pixel-text]', threshold = 4.5 } = {}) {
  const measurement = await page.locator(selector).evaluateAll((elements) => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    return elements.map((element, index) => {
      if (element.children.length) throw new Error('Pixel text specimens must contain plain text only.');
      const style = getComputedStyle(element);
      for (let node = element; node; node = node.parentElement) {
        const ancestor = getComputedStyle(node);
        if (Number(ancestor.opacity) !== 1 || ancestor.filter !== 'none' || ancestor.mixBlendMode !== 'normal') {
          throw new Error('Group opacity, foreground filters and blend modes require a dedicated compositing probe.');
        }
      }
      ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = style.webkitTextFillColor || style.color; ctx.fillRect(0, 0, 1, 1);
      const foreground = [...ctx.getImageData(0, 0, 1, 1).data];
      const range = document.createRange(); range.selectNodeContents(element);
      const rects = [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0).map(r => ({ x: r.x, y: r.y, width: r.width, height: r.height }));
      if (!rects.length || rects.some(r => r.x < 0 || r.y < 0 || r.x + r.width > innerWidth || r.y + r.height > innerHeight)) {
        throw new Error('Every pixel specimen must be fully visible in the viewport.');
      }
      return { index, text: element.textContent, foreground, rects, fontSize: style.fontSize, fontWeight: style.fontWeight };
    });
  });
  if (!measurement.length) throw new Error('No marked pixel text specimens found.');
  const rects = measurement.flatMap(item => item.rects);
  const left = Math.floor(Math.min(...rects.map(r => r.x))), top = Math.floor(Math.min(...rects.map(r => r.y)));
  const right = Math.ceil(Math.max(...rects.map(r => r.x + r.width))), bottom = Math.ceil(Math.max(...rects.map(r => r.y + r.height)));
  const clip = { x: left, y: top, width: right - left, height: bottom - top };
  const hide = await page.addStyleTag({ content: `${selector} { color: transparent !important; -webkit-text-fill-color: transparent !important; text-shadow: none !important; text-decoration-color: transparent !important; }` });
  let pixels;
  try {
    pixels = await page.screenshot({ clip, scale: 'css', animations: 'disabled' });
  } finally { await hide.evaluate(node => node.remove()); }
  const samples = await page.evaluate(async ({ png, clip, measurement, threshold }) => {
    const image = new Image(); image.src = `data:image/png;base64,${png}`; await image.decode();
    if (image.width !== clip.width || image.height !== clip.height) throw new Error('Screenshot dimensions differ from CSS pixel measurement.');
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const ctx = canvas.getContext('2d', { willReadFrequently: true }); ctx.drawImage(image, 0, 0);
    const rgba = ctx.getImageData(0, 0, image.width, image.height).data;
    const luminance = color => color.map(c => c / 255).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4).reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
    return measurement.map(item => {
      let minimum = Infinity, worst = null, count = 0;
      for (const rect of item.rects) {
        for (let y = Math.max(0, Math.floor(rect.y - clip.y)); y < Math.min(image.height, Math.ceil(rect.y + rect.height - clip.y)); y++) {
          for (let x = Math.max(0, Math.floor(rect.x - clip.x)); x < Math.min(image.width, Math.ceil(rect.x + rect.width - clip.x)); x++) {
            const at = (y * image.width + x) * 4;
            const background = [rgba[at], rgba[at + 1], rgba[at + 2]];
            const alpha = item.foreground[3] / 255;
            const foreground = item.foreground.slice(0, 3).map((c, i) => alpha * c + (1 - alpha) * background[i]);
            const a = luminance(foreground), b = luminance(background);
            const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
            if (ratio < minimum) { minimum = ratio; worst = { x: x + clip.x, y: y + clip.y, background, foreground }; }
            count++;
          }
        }
      }
      return { ...item, ratio: minimum, threshold, sampledPixels: count, worst, status: count && minimum >= threshold ? 'passed' : 'failed' };
    });
  }, { png: pixels.toString('base64'), clip, measurement, threshold });
  return { method: 'intended-text-color-against-rendered-background-line-box-pixels', samples,
    minimumRatio: Math.min(...samples.map(item => item.ratio)), status: samples.every(item => item.status === 'passed') ? 'passed' : 'failed',
    limitations: ['Conservative line-box background bound; does not measure glyph antialiasing, form placeholders, occlusion by unrelated foreground elements, or responsive states beyond the captured viewport.'] };
}

if (require.main === module) runCli('contrast_probe', inspectContrast);
module.exports = { inspectContrast, inspectTextPixels };

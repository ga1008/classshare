'use strict';

const { runCli } = require('./audit_runtime.cjs');

async function inspectGlass(page, options = {}) {
  const budget = options.budget === undefined ? null : Number(options.budget);
  if (budget !== null && (!Number.isInteger(budget) || budget < 0)) throw new Error('--budget must be a non-negative integer.');
  const result = await page.evaluate(() => {
    const layers = [];
    function label(element) {
      if (element.id) return `#${element.id}`;
      return `${element.localName}${[...element.classList].slice(0, 3).map((c) => `.${c}`).join('')}`;
    }
    function rendered(element) {
      for (let node = element; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) || Number(style.opacity) === 0 || style.contentVisibility === 'hidden') return false;
      }
      return element.getClientRects().length > 0;
    }
    function scrollAncestor(element) {
      for (let node = element.parentElement; node && node !== document.body; node = node.parentElement) {
        const style = getComputedStyle(node);
        if ((/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight)
          || (/(auto|scroll)/.test(style.overflowX) && node.scrollWidth > node.clientWidth)) return label(node);
      }
      return null;
    }
    function record(element, pseudo = '') {
      const style = getComputedStyle(element, pseudo || null);
      if (pseudo && pseudo !== '::backdrop' && ['none', 'normal'].includes(style.content)) return;
      if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) || Number(style.opacity) === 0) return;
      const backdropFilter = style.backdropFilter || style.webkitBackdropFilter || 'none';
      const filter = style.filter || 'none';
      if (backdropFilter === 'none' && !/blur\(/.test(filter)) return;
      const hostRect = element.getBoundingClientRect();
      // CSSOM has no pseudo-element bounding box. Report host bounds as an
      // estimate instead of claiming a precise pseudo-element GPU area.
      const rect = pseudo === '::backdrop'
        ? { x: 0, y: 0, width: innerWidth, height: innerHeight }
        : { x: hostRect.x, y: hostRect.y, width: hostRect.width, height: hostRect.height };
      const intersectsViewport = rect.x < innerWidth && rect.y < innerHeight && rect.x + rect.width > 0 && rect.y + rect.height > 0;
      const hit = intersectsViewport ? document.elementFromPoint(Math.max(0, Math.min(innerWidth - 1, rect.x + rect.width / 2)), Math.max(0, Math.min(innerHeight - 1, rect.y + rect.height / 2))) : null;
      const scrollingContainer = scrollAncestor(element);
      layers.push({
        host: label(element), pseudo: pseudo || null, backdropFilter, filter, rect,
        bounds: pseudo && pseudo !== '::backdrop' ? 'host-estimate' : 'measured',
        intersectsViewport,
        centerOccluded: !!hit && hit !== element && !element.contains(hit),
        scrollingContainer, insideScrollingContainer: !!scrollingContainer,
        // Occlusion is diagnostic only: covered blur hosts remain counted.
      });
    }
    for (const element of document.querySelectorAll('*')) {
      if (!rendered(element)) continue;
      record(element);
      record(element, '::before');
      record(element, '::after');
      if (element.matches('dialog:modal, [popover]:popover-open')) record(element, '::backdrop');
    }
    return {
      layers,
      visibleViewportLayerCount: layers.filter((layer) => layer.intersectsViewport).length,
      renderedLayerCount: layers.length,
      scrollingLayerCount: layers.filter((layer) => layer.insideScrollingContainer).length,
      dpr: devicePixelRatio,
      limitations: ['DOM/CSSOM render candidates; browser compositor allocations and occlusion culling require a trace.', 'Pseudo-element bounds use the host rectangle and may under/overestimate viewport intersection; pseudo hosts still count.', 'Offscreen rendered candidates remain in renderedLayerCount; no hidden-ancestor or display:none candidates count.'],
    };
  });
  return { ...result, budget, enforced: budget !== null, status: budget === null ? 'baseline' : result.renderedLayerCount <= budget ? 'passed' : 'failed' };
}

if (require.main === module) runCli('audit_glass_layers', inspectGlass);
module.exports = { inspectGlass };

import { expect, type Page, type Locator } from '@playwright/test';

export type MotionFrame = { time: number; opacity: number; a: number; d: number; x: number; y: number; scale: string; translate: string; shadow: string; border: string; width: number; height: number; visible: boolean; text: string };

// The sampler uses the native rAF saved before performance instrumentation.
// It neither stretches CSS durations nor pauses/advances the animation clock.
export async function armFrames(page: Page, selector: string, event: string | null = null, duration = 650) {
  await page.evaluate(({ selector, event, duration }) => {
    const w = window as any;
    const raf = w.__motionNativeRAF || requestAnimationFrame.bind(window);
    w.__motionFrames = new Promise(resolve => {
      const start = () => {
        const frames: any[] = [], started = performance.now();
        const tick = () => {
          const [target, pseudo] = selector.split('::');
          const node = document.querySelector<HTMLElement>(target);
          if (node) {
            const css = getComputedStyle(node, pseudo ? `::${pseudo}` : null), matrix = new DOMMatrixReadOnly(css.transform), box = node.getBoundingClientRect();
            frames.push({ time: performance.now() - started, opacity: Number(css.opacity), a: matrix.a, d: matrix.d, x: matrix.e, y: matrix.f,
              scale: css.scale, translate: css.translate, shadow: css.boxShadow, border: css.borderColor, width: box.width, height: box.height,
              visible: !node.hidden && css.display !== 'none' && css.visibility !== 'hidden' && css.contentVisibility !== 'hidden' && box.width > 0 && box.height > 0, text: node.textContent || '' });
          }
          if (performance.now() - started < duration) raf(tick); else resolve(frames);
        };
        tick();
      };
      if (event) document.addEventListener(event, start, { capture: true, once: true }); else start();
    });
  }, { selector, event, duration });
}
export const collectedFrames = (page: Page): Promise<MotionFrame[]> => page.evaluate(() => (window as any).__motionFrames);
export async function clickFrames(page: Page, trigger: Locator, selector: string) {
  await armFrames(page, selector, 'click');
  await trigger.click();
  return collectedFrames(page);
}
export function expectFade(frames: MotionFrame[], label: string) {
  expect(frames.some(frame => frame.visible && frame.opacity > .03 && frame.opacity < .97), `${label}: no actual intermediate opacity: ${JSON.stringify(frames.map(({time,opacity,visible})=>({time,opacity,visible})))}`).toBe(true);
}

import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';

const picture = (n: number) => `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000" viewBox="0 0 1600 1000"><rect width="1600" height="1000" fill="${['#edf8f4', '#eef0ff', '#fff3df'][n - 1]}"/><rect x="140" y="140" width="1320" height="720" rx="48" fill="white" stroke="#23443c" stroke-width="8"/><text x="240" y="480" font-family="sans-serif" font-size="88" fill="#193930">Network diagram ${n}</text><path d="M260 630H1280" stroke="#193930" stroke-width="16"/><circle cx="260" cy="630" r="40" fill="#0f766e"/><circle cx="1280" cy="630" r="40" fill="#0f766e"/></svg>`;
async function mount(page: Page) {
    await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://lq-lightbox.test') return route.abort();
        if (url.pathname.startsWith('/static/js/') || ['/static/css/tailwind-app.css'].includes(url.pathname)) {
            const file = path.resolve(`.${url.pathname}`);
            if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (/^\/image-[123]\.svg$/.test(url.pathname)) return route.fulfill({ contentType: 'image/svg+xml', body: picture(Number(url.pathname[7])) });
        if (url.pathname === '/broken.svg') return route.fulfill({ status: 404, body: 'fixture missing image' });
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="light" data-lq-glass="tinted" data-lq-tier="A"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ lightbox and prose</title><link rel="stylesheet" href="/static/css/tailwind-app.css"></head><body><main style="padding:24px;max-width:960px"><h1>学习资料与图片</h1><div id="gallery" data-ls-lightbox-scope data-ls-lightbox-label="第 2 题附件">${[1, 2, 3].map(n => `<button type="button" id="image-${n}" data-ls-lightbox data-ls-lightbox-group="question" data-ls-lightbox-src="/image-${n}.svg" data-ls-lightbox-title="网络实验拓扑图 ${n}：较长附件名称仍保留完整可访问文字" data-ls-lightbox-original="/image-${n}.svg?original">图 ${n}</button>`).join('')}</div><button type="button" id="show-native">原生对话框</button><section id="preview" class="lq-prose"><h2>文件预览代码</h2><div class="md-content"><pre><code>print(&quot;&lt;safe&gt;&quot;)\nprint(42)</code></pre></div></section><section class="lq-prose"><h2>材料阅读器</h2><div id="viewer-content"></div></section><section id="ai" class="lq-prose"><h2>AI 代码</h2><div class="bubble"><pre><code>console.log('&lt;safe&gt;');</code></pre></div></section><section id="outside"><h2>未迁移代码</h2><div class="md-content"><pre><code>original()</code></pre></div></section></main><dialog id="native"><button id="native-image" type="button">在原生窗口查看图片</button><iframe title="原文内容" srcdoc="<!doctype html><html><body><h1>原文</h1><input aria-label='草稿' value='未保存'><script>window.docIdentity={};<\/script></body></html>"></iframe></dialog><script src="/static/js/marked.min.js"></script><script src="/static/js/markdown_runtime.js"></script><script src="/static/js/ai_chat_component.js"></script><script type="module">
        import * as images from '/static/js/ls_image_lightbox.js'; import { getLayerSystem } from '/static/js/lq/layer.js'; import { decoratePreviewCodeBlocks } from '/static/js/file_preview.js';
        window.images=images; window.layer=getLayerSystem(document); window.copies=[]; window.notices=[]; window.requests=[]; window.showMessage=(...args)=>window.notices.push(args);window.fetch=(...args)=>{window.requests.push(args);throw Error('network forbidden')};
        Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copies.push(text);}}});
        decoratePreviewCodeBlocks(document.querySelector('#preview'));decoratePreviewCodeBlocks(document.querySelector('#outside'));
        window.ai=Object.create(window.AIChatComponent.prototype);window.ai.addCodeCopyButtons(document.querySelector('#ai .bubble'));
        window.MATERIAL_VIEWER={is_markdown:true,content:'## 材料片段\\n\\n\x60\x60\x60python\\nprint("material")\\n\x60\x60\x60'};
        await import('/static/js/material_viewer.js');
        document.querySelector('#show-native').onclick=()=>document.querySelector('#native').showModal();
        document.querySelector('#native-image').onclick=()=>images.openImageLightbox({items:[{src:'/image-1.svg',title:'原生图片'},{src:'/image-2.svg',title:'第二张'}]});
        document.body.dataset.ready='true';</script></body></html>` });
        return route.abort();
    });
    await page.goto('https://lq-lightbox.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}
async function opened(page: Page) { await expect(page.locator('.lq-lightbox')).toHaveAttribute('data-lq-layer-state', 'open'); await expect(page.locator('.lq-lightbox__img')).toHaveClass(/is-ready/); }
async function pointerHit(page: Page, selector: string) {
    expect(await page.locator(selector).evaluate(node => { const r = node.getBoundingClientRect(); const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return hit === node || node.contains(hit); })).toBe(true);
}
test.describe('LQ existing lightbox skin and prose copy', () => {
    test.beforeEach(async ({ page }) => mount(page));
    test('declarative aliases retain scope, 3-image order, original URL and safe text', async ({ page }) => {
        await page.locator('#image-2').click(); await opened(page);
        await expect(page.locator('.lq-lightbox__counter')).toHaveText('2 / 3');
        expect(await page.locator('.ls-lightbox').evaluate(root => [root, ...root.querySelectorAll('*')].every(node => [...node.classList].filter(name => name.startsWith('ls-lightbox')).every(name => node.classList.contains(name.replace('ls-lightbox', 'lq-lightbox')))))).toBe(true);
        await expect(page.locator('[data-act=original]')).toHaveAttribute('href', '/image-2.svg?original'); await expect(page.locator('[data-act=original]')).toHaveAttribute('target', '_blank'); await expect(page.locator('[data-act=original]')).toHaveAttribute('rel', 'noopener noreferrer');
        await page.locator('[data-act=next]').click(); await expect(page.locator('.lq-lightbox__counter')).toHaveText('3 / 3'); await expect(page.locator('[data-act=next]')).toBeDisabled();
        await page.keyboard.press('ArrowLeft'); await expect(page.locator('.lq-lightbox__counter')).toHaveText('2 / 3'); await page.keyboard.press('ArrowLeft'); await expect(page.locator('[data-act=prev]')).toBeDisabled();
        await page.keyboard.press('Escape'); await expect(page.locator('#image-2')).toBeFocused();
        await page.evaluate(() => (window as any).images.openImageLightbox({items:[{src:'/image-1.svg',title:'<img src=x onerror=alert(1)>',meta:'<b>plain</b>'}]})); await opened(page); await expect(page.locator('.lq-lightbox__title')).toHaveText('<img src=x onerror=alert(1)>'); await expect(page.locator('.lq-lightbox__title img')).toHaveCount(0);
    });
    test('zoom keys, real wheel, mouse drag, fit and resize retain image controller geometry', async ({ page }) => {
        await page.locator('#image-1').click(); await opened(page); const scale = page.locator('.lq-lightbox__scale'), fit = await scale.textContent();
        await page.keyboard.press('+'); await expect(scale).not.toHaveText(fit!); await page.keyboard.press('0'); await expect(scale).toHaveText(fit!);
        await page.locator('.lq-lightbox__img').dblclick(); await expect(page.locator('.lq-lightbox__stage')).toHaveClass(/is-pannable/);
        const img = page.locator('.lq-lightbox__img'), before = await img.evaluate(n => (n as HTMLElement).style.transform); const stage = await page.locator('.lq-lightbox__stage').boundingBox();
        await page.mouse.move(stage!.x + stage!.width / 2, stage!.y + stage!.height / 2); await page.mouse.down(); await page.mouse.move(stage!.x + stage!.width / 2 + 85, stage!.y + stage!.height / 2 + 60, { steps: 6 }); await page.mouse.up();
        expect(await img.evaluate(n => (n as HTMLElement).style.transform)).not.toBe(before); await expect(page.locator('.lq-lightbox')).toBeVisible();
        const beforeWheel = await scale.textContent(); await page.mouse.wheel(0, -160); await expect(scale).not.toHaveText(beforeWheel!); await page.locator('[data-act=fit]').click(); await expect(scale).toHaveText(fit!);
        await page.setViewportSize({width:390,height:844}); await expect.poll(() => scale.textContent()).not.toBe(fit); await expect(page.locator('.lq-lightbox__stage')).not.toHaveClass(/is-pannable/);
    });
    test('preview failure falls back to original, final error stays visible and next image recovers', async ({ page }) => {
        await page.evaluate(() => (window as any).images.openImageLightbox({items:[{src:'/image-1.svg',previewSrc:'/broken.svg',title:'回退原图'},{src:'/broken.svg',title:'错误图片'},{src:'/image-3.svg',title:'第三张'}]})); await opened(page); await expect(page.locator('.lq-lightbox__img')).toHaveAttribute('src','/image-1.svg');
        await page.locator('[data-act=next]').click(); await expect(page.locator('.lq-lightbox__error')).toBeVisible(); await expect(page.locator('.lq-lightbox__spinner')).toBeHidden(); await page.locator('[data-act=next]').click(); await opened(page); await expect(page.locator('.lq-lightbox__error')).toBeHidden();
    });
    test('original link still opens its native new tab without closing or replacing the viewer', async ({ page, context }) => {
        await context.route('https://lq-lightbox.test/image-2.svg?original', route=>route.fulfill({contentType:'image/svg+xml',body:picture(2)}));
        await page.locator('#image-2').click();await opened(page);const pending=page.waitForEvent('popup');await page.locator('[data-act=original]').click();const popup=await pending;
        await popup.waitForURL('https://lq-lightbox.test/image-2.svg?original');await expect(popup.locator('svg')).toBeVisible();await popup.close();await expect(page.locator('.lq-lightbox')).toBeVisible();await expect(page.locator('.lq-lightbox__counter')).toHaveText('2 / 3');
    });
    test('image tap stays open, empty stage closes and buttons ignore earlier image hit', async ({ page }) => {
        await page.setViewportSize({width:390,height:844});await page.locator('#image-2').click();await opened(page);
        await page.locator('.lq-lightbox__img').click();await expect(page.locator('.lq-lightbox')).toBeVisible();await page.locator('[data-act=next]').click();await expect(page.locator('.lq-lightbox__counter')).toHaveText('3 / 3');
        await page.locator('.lq-lightbox__stage').click({position:{x:160,y:24}});await expect(page.locator('.lq-lightbox')).toBeHidden();await expect(page.locator('#image-2')).toBeFocused();
        await page.locator('#image-2').click();await opened(page);await page.locator('.lq-lightbox__img').click();await page.locator('[data-act=close]').click();await expect(page.locator('.lq-lightbox')).toBeHidden();
    });
    test('touch pointercancel cannot navigate or close, and the next real tap works', async ({ page }) => {
        await page.setViewportSize({width:390,height:844});await page.locator('#image-2').click();await opened(page);
        await page.locator('.lq-lightbox__img').dispatchEvent('pointerdown',{pointerId:17,pointerType:'touch',clientX:300,clientY:430,button:0});
        await page.locator('.lq-lightbox__stage').dispatchEvent('pointermove',{pointerId:17,pointerType:'touch',clientX:80,clientY:430,button:0});
        await page.locator('.lq-lightbox__stage').dispatchEvent('pointercancel',{pointerId:17,pointerType:'touch',clientX:80,clientY:430,button:0});
        await expect(page.locator('.lq-lightbox__counter')).toHaveText('2 / 3');await expect(page.locator('.lq-lightbox__stage')).not.toHaveClass(/is-dragging/);await page.locator('.lq-lightbox__img').click();await expect(page.locator('.lq-lightbox')).toBeVisible();await page.locator('[data-act=prev]').click();await expect(page.locator('.lq-lightbox__counter')).toHaveText('1 / 3');
    });
    test('native dialog and iframe remain intact beneath the clickable viewer; Escape returns to native trigger', async ({ page }) => {
        await page.locator('#show-native').click(); await expect(page.frameLocator('#native iframe').getByRole('heading')).toHaveText('原文');
        await page.evaluate(() => { const f = document.querySelector<HTMLIFrameElement>('#native iframe')!; (window as any).frameDocument=f.contentDocument; });
        await page.frameLocator('#native iframe').getByRole('textbox').fill('继续保存的草稿'); await page.locator('#native-image').click(); await opened(page); expect(await page.locator('.lq-lightbox').evaluate(n => n.closest('dialog')?.id)).toBe('native'); await pointerHit(page,'.lq-lightbox__close'); await pointerHit(page,'.lq-lightbox__nav--next');
        await page.locator('.lq-lightbox__nav--next').click(); await expect(page.locator('.lq-lightbox__counter')).toHaveText('2 / 2'); await page.keyboard.press('Escape'); await expect(page.locator('#native')).toBeVisible(); await expect(page.locator('#native-image')).toBeFocused(); await expect(page.frameLocator('#native iframe').getByRole('textbox')).toHaveValue('继续保存的草稿'); expect(await page.evaluate(() => (document.querySelector('#native iframe') as HTMLIFrameElement).contentDocument === (window as any).frameDocument)).toBe(true);
    });
    test('20 open-close cycles keep one root and release active listeners/locks; destroy disposes delegation', async ({ page }) => {
        expect(await page.evaluate(async () => { const w=window as any; w.images.destroyImageLightbox();w.layer.destroy(); const live:any[]=[]; const add=EventTarget.prototype.addEventListener,remove=EventTarget.prototype.removeEventListener;
            EventTarget.prototype.addEventListener=function(t,l,o){ if(this===document||this===window){const c=typeof o==='boolean'?o:!!o?.capture;if(!live.some(x=>x[0]===this&&x[1]===t&&x[2]===l&&x[3]===c))live.push([this,t,l,c]);} return add.call(this,t,l,o);};
            EventTarget.prototype.removeEventListener=function(t,l,o){const c=typeof o==='boolean'?o:!!o?.capture;const i=live.findIndex(x=>x[0]===this&&x[1]===t&&x[2]===l&&x[3]===c);if(i>=0)live.splice(i,1);return remove.call(this,t,l,o);};
            const dispose=w.images.bindImageLightboxDelegation(document);const same=dispose===w.images.bindImageLightboxDelegation(document);let maxRoots=0;
            for(let i=0;i<20;i++){ document.querySelector<HTMLElement>('#image-1')!.focus(); w.images.openImageLightbox({items:[{src:'/image-1.svg'}]});maxRoots=Math.max(maxRoots,document.querySelectorAll('.lq-lightbox').length);await w.images.closeImageLightbox(); }
            w.images.destroyImageLightbox();w.layer.destroy();document.querySelector<HTMLElement>('#image-1')!.click();const result={same,maxRoots,live:live.length,roots:document.querySelectorAll('.ls-lightbox').length,open:w.images.isImageLightboxOpen(),lock:document.body.style.overflow,inert:document.querySelectorAll('[inert]').length};EventTarget.prototype.addEventListener=add;EventTarget.prototype.removeEventListener=remove;return result;
        })).toEqual({same:true,maxRoots:1,live:0,roots:0,open:false,lock:'',inert:0});
    });
    test('loadToken still rejects late callbacks after destruction and reopening', async ({ page }) => {
        expect(await page.evaluate(async () => {const w=window as any,Native=window.Image,probes:any[]=[];window.Image=class{naturalWidth=120;naturalHeight=80;onload:any;onerror:any;constructor(){probes.push(this);}set src(_v:string){}} as any;
            w.images.openImageLightbox({items:[{src:'/old.svg'}]});w.images.destroyImageLightbox();w.images.openImageLightbox({items:[{src:'/new.svg',title:'新图'}]});probes[0].onload();probes[0].onerror();const stale=!document.querySelector('.lq-lightbox__img')!.hasAttribute('src');probes[1].onload();const fresh=document.querySelector('.lq-lightbox__img')!.getAttribute('src');await w.images.closeImageLightbox();probes[1].onload();const closed=!document.querySelector('.lq-lightbox__img')!.hasAttribute('src');window.Image=Native;return{stale,fresh,closed};})).toEqual({stale:true,fresh:'/new.svg',closed:true});
    });
    test('real file preview/material viewer/AI copy methods retain payload and reset behavior without new controller', async ({ page }) => {
        await expect(page.locator('#viewer-content .materials-code-copy-btn')).toBeVisible(); await page.clock.install();
        await page.locator('#preview .materials-code-copy-btn').click(); await expect(page.locator('#preview .materials-code-copy-btn')).toBeDisabled(); await expect(page.locator('#preview .materials-code-copy-btn')).toHaveClass(/is-copied/);
        await page.locator('#viewer-content .materials-code-copy-btn').click(); await page.locator('#ai .copy-code-btn').click();
        expect(await page.evaluate(() => (window as any).copies)).toEqual(['print("<safe>")\nprint(42)','print("material")\n',"console.log('<safe>');"]);
        await page.clock.runFor(2100);await expect(page.locator('#preview .materials-code-copy-btn')).toBeEnabled();await expect(page.locator('#preview .materials-code-copy-btn')).toHaveText('复制');await expect(page.locator('#ai .copy-code-btn')).toHaveText('复制');expect(await page.evaluate(() => (window as any).requests)).toEqual([]);
    });
    test('prose styles are scoped; copied state, contrast and code clearance stay usable', async ({ page }) => {
        expect(await page.locator('#outside .materials-code-copy-btn').evaluate(n=>getComputedStyle(n).opacity)).toBe('0.82');
        const result=await page.locator('#preview .materials-code-copy-btn').evaluate(n=>{const s=getComputedStyle(n),r=n.getBoundingClientRect(),code=n.parentElement!.querySelector('code')!.getBoundingClientRect();return{opacity:s.opacity,blur:s.backdropFilter,bottom:r.bottom,codeTop:code.top};});expect(result.opacity).toBe('1');expect(result.blur).toBe('none');expect(result.bottom).toBeLessThanOrEqual(result.codeTop);
        await page.locator('#preview .materials-code-copy-btn').focus();await page.keyboard.press('Enter');await expect(page.locator('#preview .materials-code-copy-btn')).toHaveClass(/is-copied/);
        const resultAxe=await new AxeBuilder({page}).include('.lq-prose').analyze();expect(resultAxe.violations.filter(v=>['serious','critical'].includes(v.impact||''))).toEqual([]);
    });
    test('clipboard failure retains original code and the existing sanitizer still removes executable markup',async({page})=>{
        await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('fixture clipboard rejection');}}}));
        await page.locator('#preview .materials-code-copy-btn').click();await expect(page.locator('#preview .materials-code-copy-btn')).toBeEnabled();await expect(page.locator('#preview .materials-code-copy-btn')).toHaveText('复制');await expect(page.locator('#preview code')).toHaveText('print("<safe>")\nprint(42)');
        expect(await page.evaluate(()=>{const w=window as any;const root=document.createElement('div');root.className='lq-prose';root.innerHTML=w.MarkdownRuntime.parse('<script>window.bad=true</script>\n<a href="javascript:alert(1)" onclick="window.bad=true">链接</a>\n\n```html\n<b>literal</b>\n```');return{scripts:root.querySelectorAll('script,[onclick],[href^="javascript:"]').length,code:root.querySelector('code')?.textContent,bad:!!w.bad};})).toEqual({scripts:0,code:'<b>literal</b>\n',bad:false});
    });
    for(const appearance of ['light','dark']) for(const palette of ['teal','indigo','sky','mint','violet','rose']) for(const width of [390,1440]) test(`${palette} ${appearance} ${width}: readable full viewer/prose, single material and axe`,async({page})=>{
        await page.setViewportSize({width,height:width===390?844:980});await page.evaluate(({appearance,palette})=>{document.documentElement.dataset.appearance=appearance;document.documentElement.dataset.uiPalette=palette;},{appearance,palette});
        const prose=await new AxeBuilder({page}).include('.lq-prose').analyze();expect(prose.violations.filter(v=>['serious','critical'].includes(v.impact||''))).toEqual([]);await page.locator('#image-2').click();await opened(page);await pointerHit(page,'.lq-lightbox__close');await pointerHit(page,'.lq-lightbox__nav--next');
        expect(await page.locator('.lq-lightbox').evaluate(root=>[...root.querySelectorAll('*'),root].filter(n=>getComputedStyle(n).backdropFilter!=='none').map(n=>n.classList.contains('lq-lightbox__bar')))).toEqual([true]);
        expect(await page.locator('.lq-lightbox').evaluate(n=>n.scrollWidth<=n.clientWidth)).toBe(true);const audit=await new AxeBuilder({page}).include('.lq-lightbox').analyze();expect(audit.violations.filter(v=>['serious','critical'].includes(v.impact||''))).toEqual([]);
        await page.locator('[data-act=zoom-out]').hover();expect(await page.locator('[data-act=zoom-out]').evaluate(n=>{const c=getComputedStyle(n).backgroundColor;return !c.startsWith('rgba')&&!c.includes('/');})).toBe(true);
        if(palette==='teal'){fs.mkdirSync('.codex-temp/lq-audit/s2/lightbox',{recursive:true});await page.mouse.move(0,0);await page.screenshot({path:`.codex-temp/lq-audit/s2/lightbox/${appearance}-${width}.png`});await page.keyboard.press('Escape');await expect(page.locator('.lq-lightbox')).toBeHidden();await page.screenshot({path:`.codex-temp/lq-audit/s2/lightbox/prose-${appearance}-${width}.png`});}
    });
    test('coarse touch, forced colors, reduced motion and contrast/off fallbacks preserve control access', async ({ browser })=>{
        const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true,forcedColors:'active',reducedMotion:'reduce'});const page=await context.newPage();await mount(page);await page.locator('#image-2').tap();await opened(page);
        expect(await page.locator('.lq-lightbox [data-act]').evaluateAll(nodes=>nodes.filter(n=>!(n as HTMLElement).hidden).every(n=>{const r=n.getBoundingClientRect();return r.width>=44&&r.height>=44;}))).toBe(true);await page.locator('[data-act=next]').tap();await expect(page.locator('.lq-lightbox__counter')).toHaveText('3 / 3');await page.locator('[data-act=close]').tap();expect(await page.locator('.lq-prose button').evaluateAll(ns=>ns.every(n=>n.getBoundingClientRect().height>=44))).toBe(true);
        await page.locator('#preview .materials-code-copy-btn').tap();await expect(page.locator('#preview .materials-code-copy-btn')).toHaveClass(/is-copied/);await context.close();
    });
    for(const mode of ['off','B','C','contrast']) test(`glass ${mode} fallback retains readable chrome`,async({page})=>{
        await page.evaluate(mode=>{if(mode==='off')document.documentElement.dataset.lqGlass='off';else if(mode==='contrast')document.documentElement.dataset.lqContrast='more';else document.documentElement.dataset.lqTier=mode;},mode);await page.locator('#image-2').click();await opened(page);
        if(['off','C'].includes(mode))expect(await page.locator('.lq-lightbox').evaluate(n=>[n,...n.querySelectorAll('*')].every(x=>getComputedStyle(x).backdropFilter==='none'))).toBe(true);
        const audit=await new AxeBuilder({page}).include('.lq-lightbox').analyze();expect(audit.violations.filter(v=>['serious','critical'].includes(v.impact||''))).toEqual([]);await pointerHit(page,'.lq-lightbox__close');
        await page.evaluate(()=>(window as any).images.openImageLightbox({items:[{src:'/broken.svg',title:'错误状态'}]}));await expect(page.locator('.lq-lightbox__error')).toBeVisible();
        const errorAudit=await new AxeBuilder({page}).include('.lq-lightbox').analyze();expect(errorAudit.violations.filter(v=>['serious','critical'].includes(v.impact||''))).toEqual([]);
    });
});

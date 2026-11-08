import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';

const manifest=JSON.parse(fs.readFileSync('static/assets/manifest.json','utf8'));
const asset=(name:string)=>'/static/'+manifest.entries[name];
const python=process.env.LQ_TEST_PYTHON||(process.platform==='win32'?'C:/Python314/python.exe':'python3');
const samples=JSON.parse(execFileSync(python,['-c',`import json,runpy
h=runpy.run_path('tests/e2e/scripts/render_lq_content.py')
_,env=h['environment']()
m=env.get_template('macros/lq/content.html').module
cases=[]
for variant in ['default','flat','stat','hero']:
 p={'title':'Shared Card '+variant,'meta':'Material-paired title','variant':variant}
 if variant=='stat':p['value']=200
 cases.append({'props':p,'html':str(m.lq_card(**p))})
print(json.dumps(cases))`],{encoding:'utf8'}));

for(const palette of ['teal','indigo','sky','mint','violet','rose'])for(const appearance of ['light','dark']){
 test(`Card title follows its actual material ${palette}/${appearance} across variants, renderers and narrow/wide views`,async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('https://card-ink.test/**',route=>{
   const u=new URL(route.request().url());
   if(u.pathname.startsWith('/static/')){const file=path.resolve('.'+u.pathname);if(!file.startsWith(path.resolve('static')+path.sep)||!fs.existsSync(file))return route.fulfill({status:404});return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.css')?'text/css':'text/javascript'});}
   return route.fulfill({contentType:'text/html; charset=utf-8',body:`<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="${palette}" data-appearance="${appearance}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><style>main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,16rem),1fr));gap:16px;padding:24px}.sample{min-width:0}</style></head><body><main id="samples" class="classroom-workspace-v2"></main><script type="module">import * as content from '${asset('js/lq/content.js')}';const data=${JSON.stringify(samples)};for(const entry of ['jinja','html','element'])for(const tone of ['auto','light','dark'])for(const item of data){const wrapper=document.createElement('section');wrapper.className='sample';if(entry==='element')wrapper.append(content.createContent('card',item.props));else wrapper.innerHTML=entry==='jinja'?item.html:content.contentMarkup('card',item.props);const card=wrapper.querySelector('.lq-card');card.dataset.inkSample=entry+'-'+tone+'-'+item.props.variant;if(tone!=='auto')card.dataset.lqTone=tone;document.querySelector('#samples').append(wrapper);}window.ready=true;</script></body></html>`});
  });
  await page.goto('https://card-ink.test');await page.waitForFunction(()=>(window as any).ready);
  await expect(page.locator('[data-ink-sample]')).toHaveCount(36);
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:980});
   const measurements=await page.locator('[data-ink-sample]').evaluateAll(cards=>cards.map(card=>({sample:(card as HTMLElement).dataset.inkSample,cardInk:getComputedStyle(card).color,titleInk:getComputedStyle(card.querySelector('.lq-card__title')!).color,width:card.getBoundingClientRect().width,viewport:innerWidth})));
   for(const row of measurements){expect(row.titleInk,row.sample).toBe(row.cardInk);expect(row.width,row.sample).toBeLessThanOrEqual(width);}
  }
  if(palette==='teal'&&appearance==='dark')await page.screenshot({path:'.codex-temp/lq-card-title-ink-dark.png'});
  expect(errors).toEqual([]);
 });
}

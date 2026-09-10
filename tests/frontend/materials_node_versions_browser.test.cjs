/* Actual Chromium executes the production picker/move/delete functions.
 * DOM/API are synthetic; this checks interaction timing, not live deployment. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/materials_manage.js'), 'utf8');
const code = ['loadFolderOptions', 'openMoveModal', 'submitMove', 'deleteActiveMaterial'].map(name => {
    const found = source.match(new RegExp(`^(?:async )?function ${name}\\([\\s\\S]*?^}`, 'm'));
    assert.ok(found, name);
    return found[0];
}).join('\n');

test('material move versions, delayed picker, 409 recovery and delete confirmation run in Chromium', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        await page.setContent('<select id="target"></select><button id="submit">Move</button><p id="name"></p><p id="path"></p><p id="status"></p>');
        await page.addScriptTag({ content: `
            window.state = {activeDetail:{id:10,name:'Old display',node_type:'file',updated_at:'old'},move:{},currentParentId:null,detailRequestId:0};
            window.refs = {moveTarget:document.querySelector('#target'),moveSubmitBtn:document.querySelector('#submit'),
                moveName:document.querySelector('#name'),movePath:document.querySelector('#path'),moveStatus:document.querySelector('#status')};
            window.calls=[];window.pending=[];window.confirmResult=true;window.conflict=false;window.structuralBlocker=null;window.confirmOptions=null;
            window.escapeHtml = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
            window.apiFetch = async (url,options={}) => {
                calls.push({url,options});
                if(url.endsWith('/attributes')) return {material:{id:Number(url.split('/')[3]),name:'Current source',node_type:'file',material_path:'Root/current.md',updated_at:'source-v2'}};
                if(url.includes('folder-options')) return await new Promise(resolve=>pending.push(resolve));
                if(url.endsWith('/move')) {if(conflict)throw Object.assign(new Error('Changed'),{status:409});return {status:'success'};}
                if(url.endsWith('/delete-impact'))return {impact:{total_reference_count:0,impact_token:'f'.repeat(64),subtree:{registered_pack_count:2},structural_blocker:structuralBlocker}};
                return {status:'success'};
            };
            window.setModalStatus=(element,message)=>element.textContent=message;
            window.openModal=()=>{};window.closeModal=()=>{};window.closeDetailModal=()=>{};window.renderDetail=()=>{};
            window.showToast=()=>{};window.loadLibrary=async()=>{};window.openProcessMaterialConfirm=async options=>{confirmOptions=options;return confirmResult;};
            window.openMaterialDeleteImpactConfirm=async()=>confirmResult;
            ${code}
        ` });
        await page.evaluate(() => openMoveModal());
        await page.waitForFunction(() => pending.length === 1);
        assert.equal(await page.locator('#submit').isDisabled(), true);
        await page.evaluate(() => submitMove());
        assert.equal(await page.evaluate(() => calls.some(call => call.options.method === 'POST')), false);
        await page.evaluate(() => pending.shift()({folders:[{id:20,name:'Target',material_path:'Target',updated_at:'target-v4'}]}));
        await page.waitForFunction(() => state.move.optionsReady);
        await page.selectOption('#target', '20');
        await page.evaluate(() => {conflict=true;return submitMove();});
        assert.deepEqual(await page.evaluate(() => calls.find(call=>call.options.method==='POST').options.body), {
            target_parent_id:20,expected_updated_at:'source-v2',expected_target_updated_at:'target-v4'
        });
        assert.equal(await page.locator('#submit').isDisabled(),true);
        assert.match(await page.locator('#status').innerText(),/重新选择/);
        await page.evaluate(() => {openMoveModal();});
        await page.waitForFunction(() => pending.length === 1);
        await page.evaluate(() => {state.activeDetail={id:11,name:'Next',node_type:'file'};openMoveModal();});
        await page.waitForFunction(() => pending.length === 2);
        await page.evaluate(() => pending[1]({folders:[{id:30,name:'Current target',material_path:'Current',updated_at:'target-v8'}]}));
        await page.waitForFunction(() => state.move.optionsReady);
        await page.evaluate(() => pending[0]({folders:[{id:99,name:'Late old target',material_path:'Old',updated_at:'old'}]}));
        assert.equal(await page.locator('#target').locator('option[value="30"]').count(),1);
        assert.equal(await page.locator('#target').locator('option[value="99"]').count(),0);
        await page.evaluate(() => {confirmResult=false;return deleteActiveMaterial();});
        assert.equal(await page.evaluate(() => calls.filter(call=>call.options.method==='DELETE').length),0);
        assert.match(await page.evaluate(() => confirmOptions.detail), /2 个学习文档包将归档/);
        await page.evaluate(() => {confirmResult=true;structuralBlocker='不能单独删除学习文档包内部材料';return deleteActiveMaterial();});
        assert.equal(await page.evaluate(() => calls.filter(call=>call.options.method==='DELETE').length),0);
        await page.evaluate(() => {structuralBlocker=null;});
        await page.evaluate(() => {confirmResult=true;return deleteActiveMaterial();});
        const deletion = await page.evaluate(() => calls.find(call=>call.options.method==='DELETE'));
        assert.equal(new URL(deletion.url,'https://synthetic.test').searchParams.get('impact_token'),'f'.repeat(64));
        await page.close();
    } finally { await browser.close(); }
});

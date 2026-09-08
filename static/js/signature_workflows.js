import { apiFetch } from './api.js';

const root = document.querySelector('[data-signature-workflows]');
const $ = selector => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const statusText = value => ({pending:'待审批',approved:'已批准',consumed:'已使用',partially_used:'部分使用',rejected:'已拒绝',cancelled:'已取消',applied:'已应用到文档',queued:'等待更新文档',waiting:'等待全部批准',manual:'等待申请人应用',failed:'应用失败'}[value] || value);
const badge = value => `<span class="msw-badge msw-badge--${esc(value)}">${esc(statusText(value))}</span>`;
const initial = new URLSearchParams(location.search);
const state = {view:initial.get('view') === 'outgoing' ? 'outgoing' : 'incoming',offset:0,total:0,items:[],selected:new Set(),current:0,sequence:0,busy:false};
let debounce;
const call = (url, options={}) => apiFetch('/api/signatures'+url,{...options,silent:true});
const post = (url, data) => call(url,{method:'POST',body:JSON.stringify(data)});
const report = text => { $('[data-status]').textContent=text; };
function selectionChanged(){
    $('[data-count]').textContent=`${state.total} 条申请 · 已选 ${state.selected.size} 条`;
    root.querySelectorAll('[data-batch]').forEach(button=>{button.disabled=!state.selected.size||state.busy;button.hidden=state.view!=='incoming';});
    const eligible=state.items.filter(item=>item.can_review);
    $('[data-select-page]').checked=eligible.length>0&&eligible.every(item=>state.selected.has(item.id));
    $('[data-select-page]').indeterminate=eligible.some(item=>state.selected.has(item.id))&&!$('[data-select-page]').checked;
    $('[data-select-page]').disabled=state.view!=='incoming'||!eligible.length;
}
function renderList(){
    $('[data-list]').innerHTML=state.items.length?state.items.map(item=>`<article class="msw-request ${item.id===state.current?'is-active':''}" data-request="${item.id}" tabindex="0" aria-label="查看${esc(item.context_label||item.signature_subject_name)}">
        <div class="msw-request__top">${state.view==='incoming'&&item.can_review?`<input type="checkbox" data-select="${item.id}" aria-label="选择申请 ${item.id}" ${state.selected.has(item.id)?'checked':''}>`:''}<strong>${esc(item.requester_name)}</strong>${badge(item.status)}</div>
        <h3>${esc(item.context_label||'签名'+(item.request_kind==='claim'?'认领':'使用')+'申请')}</h3><p>${esc(item.signature_subject_name)} · ${esc(item.items.map(point=>point.function_point_label).join('、'))}</p>
        <p>${esc([item.requester_college,item.requester_department].filter(Boolean).join(' · '))} · ${esc(item.requested_at)}</p>
        ${state.view==='outgoing'&&item.apply_status?`<div>${badge(item.apply_status)}</div>`:''}</article>`).join(''):'<div class="msw-placeholder"><strong>暂无匹配申请</strong><p>调整筛选条件，或稍后刷新查看。</p></div>';
    $('[data-page]').textContent=`第 ${Math.floor(state.offset/50)+1} 页 · 共 ${state.total} 条`;
    $('[data-prev]').disabled=state.offset===0;
    $('[data-next]').disabled=state.offset+50>=state.total;
    selectionChanged();
}
async function load({preserveMessage=false}={}){
    const seq=++state.sequence;
    if(!preserveMessage)report('正在读取…');
    try{
        if(state.view==='usage'){
            const data=await call('/usage-logs?limit=200');if(seq!==state.sequence)return;
            $('[data-list]').innerHTML=(data.items||[]).map(item=>`<article class="msw-request"><strong>${esc(item.signature_name)}</strong><h3>${esc(item.context_label||'材料签名')}</h3><p>${esc(item.used_by_name)} · ${esc(item.used_at)}</p><p>${esc(item.authorization_mode==='approval'?'经申请批准使用':'直接使用')}</p></article>`).join('')||'<div class="msw-placeholder">暂无使用记录</div>';
            $('[data-count]').textContent=`最近 ${data.items?.length||0} 条使用记录`;report('');return;
        }
        $('[data-history]').hidden=state.view!=='outgoing';
        if(state.view==='outgoing'){
            const history=await call('/materials/applications');if(seq!==state.sequence)return;
            $('[data-history-list]').innerHTML=(history.batches||[]).map(batch=>`<div class="msw-note"><strong>批量提交 ${esc(batch.created_at)}</strong> · ${esc({preparing:'正在准备',completed:'已提交',partial:'部分提交',failed:'提交失败'}[batch.status]||batch.status)}${batch.results.filter(item=>item.status==='failed').map(item=>`<p>${esc(item.title)}：${esc(item.error)}</p>`).join('')}</div>`).join('')+(history.flows||[]).map(flow=>`<div class="msw-toolbar"><span>${esc(flow.material_label)} · ${esc(flow.function_point_key.includes('dean')?'教学院长':flow.function_point_key.includes('department')||flow.function_point_key.includes('reviewer')?'系部审核':'教师签名')}</span>${badge(flow.apply_status||flow.status)}${flow.status==='approved'&&['manual','failed'].includes(flow.apply_status)?`<button class="msw-button" data-apply="${flow.id}">应用配置</button>`:''}<span class="msw-note">${esc(flow.apply_error)}</span></div>`).join('');
        }
        const params=new URLSearchParams(new FormData($('[data-filters]')));
        params.set('direction',state.view);params.set('offset',state.offset);params.set('limit',50);
        if(initial.get('batch_id'))params.set('batch_id',initial.get('batch_id'));
        const data=await call('/requests?'+params);if(seq!==state.sequence)return;
        state.items=data.items;state.total=data.total;state.selected=new Set([...state.selected].filter(id=>state.items.some(item=>item.id===id&&item.can_review)));
        renderList();if(!preserveMessage)report('');
    }catch(error){if(seq===state.sequence)report(error.message);}
}
async function openRequest(id){
    state.current=id;renderList();
    $('[data-preview]').innerHTML='<div class="msw-placeholder">正在读取申请文档…</div>';
    try{
        const detail=await call(`/requests/${id}`);if(id!==state.current)return;
        const item=detail.request;
        $('[data-preview]').innerHTML=`<header class="msw-preview__head"><h3>${esc(item.context_label||'签名申请')}</h3><div class="msw-inline">${badge(item.status)}<span>${esc(item.requester_name)} · ${esc(item.requested_at)}</span></div><p class="msw-note">${esc(item.request_note||'申请人未填写附言')}</p></header>
            ${detail.preview_url?`<iframe src="${esc(detail.preview_url)}" title="申请时的实际文档"></iframe>`:`<div class="msw-placeholder">${item.request_kind==='claim'?`<img src="/api/signatures/${Number(item.signature_id)}/image" alt="申请认领的签名" style="max-width:220px;max-height:100px;object-fit:contain"><p>此申请用于认领签名。批准后，签名归属将转移并绑定申请人的账号。</p>`:esc(detail.preview_notice)}</div>`}
            <section class="msw-preview__actions"><div><strong>${esc(item.signature_subject_name)}</strong><span class="msw-note">${esc(item.items.map(point=>point.function_point_label).join('、'))}</span></div>
                <label class="msw-field">审批意见（可选）<input maxlength="300" data-review-note placeholder="给申请人留下说明"></label>
                <div>${item.can_review?`<button class="msw-button msw-primary" data-review="approve" data-id="${id}" data-snapshot="${esc(item.snapshot_id)}">批准该签名</button><button class="msw-button" data-review="reject" data-id="${id}">拒绝</button>`:''}
                ${item.can_review&&item.snapshot_id?`<button class="msw-button" data-approve-document="${id}" data-snapshot="${esc(item.snapshot_id)}">批准本文件待我审批的签名</button>`:''}
                ${state.view==='outgoing'&&item.status==='pending'?`<button class="msw-button" data-cancel="${id}">取消申请</button>`:''}
                ${state.view==='outgoing'&&item.flow_id&&['manual','failed'].includes(item.apply_status)?`<button class="msw-button msw-primary" data-apply="${item.flow_id}">应用已批准配置</button>`:''}</div>
                <p class="msw-note">${esc(item.apply_error||item.invalidation_reason||'授权仅对这份材料当前内容版本和指定签名位置有效。')}</p></section>`;
    }catch(error){if(id===state.current)$('[data-preview]').innerHTML=`<div class="msw-placeholder">${esc(error.message)}</div>`;}
}
async function review(ids,action,expectedSnapshot,wholeDocument=false){
    if(state.busy||!ids.length)return;
    state.busy=true;selectionChanged();
    const note=$('[data-review-note]')?.value||'';
    try{
        const result=wholeDocument?await post(`/requests/${ids[0]}/document-review`,{action,note,expected_snapshot_id:expectedSnapshot}):ids.length===1?await post(`/requests/${ids[0]}/${action}`,{note,expected_snapshot_id:expectedSnapshot}):await post('/requests/batch-review',{request_ids:ids,action,note});
        const failed=(result.items||[]).filter(item=>item.status==='error'||item.success===false||item.error);
        report(failed.length?`处理完成，${failed.length} 项未成功：${failed.map(item=>item.message||item.error).join('；')}`:`已${action==='approve'?'批准':'处理'} ${result.processed??ids.length} 项申请。`);
        state.selected.clear();await load({preserveMessage:true});if(state.current)await openRequest(state.current);
    }catch(error){report(error.message);}finally{state.busy=false;selectionChanged();}
}
root.addEventListener('click',async event=>{
    const view=event.target.closest('[data-view]');
    if(view){state.view=view.dataset.view;state.offset=0;state.selected.clear();state.current=0;
        root.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button===view)));
        $('[data-filters]').hidden=state.view==='usage';$('[data-history]').hidden=state.view!=='outgoing';$('[data-preview]').hidden=state.view==='usage';$('.msw-workspace').style.gridTemplateColumns=state.view==='usage'?'minmax(0,1fr)':'';$('[data-select-page]').closest('label').hidden=state.view==='usage';$('[data-preview]').innerHTML='<div class="msw-placeholder"><strong>选择一份申请材料</strong><p>查看文档及签名处理结果。</p></div>';$('.msw-pagination').hidden=state.view==='usage';
        $('[name=status]').value=state.view==='incoming'?'pending':'';selectionChanged();await load();return;}
    const batch=event.target.closest('[data-batch]');if(batch){await review([...state.selected],batch.dataset.batch);return;}
    const action=event.target.closest('[data-review]');if(action){await review([Number(action.dataset.id)],action.dataset.review,action.dataset.snapshot);return;}
    const whole=event.target.closest('[data-approve-document]');if(whole){await review([Number(whole.dataset.approveDocument)],'approve',whole.dataset.snapshot,true);return;}
    const apply=event.target.closest('[data-apply]');const cancel=event.target.closest('[data-cancel]');
    if(apply||cancel){try{await post(apply?`/flows/${apply.dataset.apply}/apply`:`/requests/${cancel.dataset.cancel}/cancel`,{});report(apply?'签名已应用到文档。':'申请已取消。');await load({preserveMessage:true});if(state.current)await openRequest(state.current);}catch(error){report(error.message);}return;}
    if(event.target.closest('[data-refresh]')){await load();return;}
    if(event.target.closest('[data-prev]')){state.offset=Math.max(0,state.offset-50);await load();return;}
    if(event.target.closest('[data-next]')){state.offset+=50;await load();return;}
    if(event.target.closest('input,button,a'))return;
    const row=event.target.closest('[data-request]');if(row)await openRequest(Number(row.dataset.request));
});
root.addEventListener('change',event=>{
    if(event.target.matches('[data-select]')){const id=Number(event.target.dataset.select);event.target.checked?state.selected.add(id):state.selected.delete(id);selectionChanged();}
    if(event.target.matches('[data-select-page]')){state.selected=new Set(event.target.checked?state.items.filter(item=>item.can_review).map(item=>item.id):[]);renderList();}
});
root.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)&&event.target.matches('[data-request]')){event.preventDefault();openRequest(Number(event.target.dataset.request));}});
$('[data-filters]').addEventListener('submit',event=>event.preventDefault());
$('[data-filters]').addEventListener('input',()=>{clearTimeout(debounce);debounce=setTimeout(()=>{state.offset=0;load();},250);});
$('[data-filters]').addEventListener('reset',()=>{initial.delete('batch_id');setTimeout(()=>{state.offset=0;load();},0);});
if(state.view==='outgoing'){$('[name=status]').value='';root.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===state.view)));}
await load();
if(initial.get('request_id'))await openRequest(Number(initial.get('request_id')));

if(initial.has('start'))$('[data-start-application]').open=true;
try{const readiness=await call('/notification-readiness');if(readiness.email_reason)$('[data-notification-readiness]').textContent='系统消息已接入签名流程。'+readiness.email_reason;}catch{/* Inbox remains usable when email status cannot be read. */}

/** Owner-only history picker; historical context never impersonates a live lesson. */
import { apiFetch } from './api.js';
import { openDialog } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { createForm } from './lq/forms.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { createDomainButton } from './lq/domain-controls.js';

export function openAttendanceHistory({ classOfferingId, trigger, parentLayer }) {
    const body = document.createElement('div'); body.className = 'la-workspace';
    const message = document.createElement('p'); message.setAttribute('role','status');
    const field = createForm('select',{id:`la-history-${crypto.randomUUID()}`,label:'历史点名',value:'',options:[{value:'',label:'选择原课次记录'}]});
    const select = field.querySelector('select'), controls = document.createElement('div'); controls.className = 'la-actions';
    let closed=false, loading=false, rows=[], request=null, nextBeforeId=null;
    const view=createDomainButton({label:'查看所选历史',variant:'prominent',size:'sm'}), retry=createDomainButton({label:'最新历史记录',variant:'glass',size:'sm'}), older=createDomainButton({label:'更早记录',variant:'ghost',size:'sm'});
    controls.append(view,retry,older); body.append(message,field,controls);
    const top=getLayerSystem(document).top();
    const handle=openDialog({title:'历史点名',body,size:'md'},{trigger,...(parentLayer?{parentLayer}:top?.root.contains(trigger)?{parentLayer:top}:{}),onClose:cleanup,onDestroy:cleanup});
    const dropdowns=enhanceDropdowns(body);
    function cleanup(){if(closed)return;closed=true;request?.abort();dropdowns.forEach(d=>d.destroy());}
    function gate(){view.disabled=closed||loading||!select.value;retry.disabled=loading;older.disabled=loading||!nextBeforeId;select.disabled=loading;}
    select.addEventListener('change',gate);
    async function load(beforeId=null){if(loading||closed)return;loading=true;gate();request=new AbortController();const deadline=window.setTimeout(()=>request.abort(),20000);
        try{const query=new URLSearchParams({limit:'50'});if(beforeId)query.set('before_id',String(beforeId));const value=await apiFetch(`/api/classrooms/${classOfferingId}/login-attendance/history?${query}`,{silent:true,signal:request.signal});if(closed)return;rows=value.items||[];nextBeforeId=value.next_before_id||null;
            select.replaceChildren(new Option('选择原课次记录',''),...rows.map(row=>{const c=row.context||{};return new Option([`${c.session_date||''} ${c.session_title||c.course_name||'历史课次'}`.trim(),c.class_name||c.teaching_class_name,`点名 ${row.id}`].filter(Boolean).join(' · '),String(row.id));}));
            message.textContent=rows.length?`本页 ${rows.length} 轮${nextBeforeId?'，可继续读取更早记录':''}；合班迁移记录保留原日期与学生班名。`:'暂无历史点名记录。';message.setAttribute('role','status');
        }catch(error){if(!closed){message.textContent=error.name==='AbortError'?'读取未完成，请重试。':error.message||'读取失败，请重试。';message.setAttribute('role','alert');}}
        finally{window.clearTimeout(deadline);loading=false;if(!closed)gate();}}
    retry.addEventListener('click',()=>load());older.addEventListener('click',()=>load(nextBeforeId));
    view.addEventListener('click',async()=>{const row=rows.find(row=>String(row.id)===select.value);if(!row||loading||closed)return;loading=true;gate();try{const mod=await import('./login_attendance.js');if(!closed){const result=mod.openLoginAttendance({classOfferingId,session:null,userRole:'teacher',trigger:view,historyOnly:true,attendanceId:row.id});if(result?.handle===parentLayer)await getLayerSystem(document).close(handle,'already-open');}}catch(error){if(!closed)message.textContent=error.message||'历史记录界面加载失败。';}finally{loading=false;if(!closed)gate();}});
    void load();return handle;
}

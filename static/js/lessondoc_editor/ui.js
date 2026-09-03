import {createPopoverSystem} from '../ui_popover.js';
export const popovers=createPopoverSystem();
export function el(tag, className='', text='') { const node=document.createElement(tag);if(className)node.className=className;if(text)node.textContent=text;return node; }
export function button(label, action, className='lde-button') { const b=el('button',className,label);b.type='button';b.addEventListener('click',action);return b; }
export function field(label,value,change,options={}) {
    const wrap=el('label','lde-field'),caption=el('span','lde-field-label',label);wrap.append(caption);
    let input;
    if(options.choices){input=el('select');if(value==null)input.append(new Option(options.mixed?'混合值':'默认',''));for(const [v,t] of Object.entries(Array.isArray(options.choices)?Object.fromEntries(options.choices.map(x=>[x,x])):options.choices))input.append(new Option(t,v));input.value=value??'';}
    else if(options.multiline){input=el('textarea');input.rows=options.rows||3;input.value=value??'';input.spellcheck=false;}
    else{input=el('input');input.type=options.type||'text';if(input.type==='checkbox'){input.checked=!!value;input.indeterminate=!!options.mixed;}else input.value=value??'';}
    if(options.mixed)input.placeholder='混合值';if(options.min!=null)input.min=options.min;if(options.max!=null)input.max=options.max;if(options.step!=null)input.step=options.step;
    // Text/number fields commit valid input while focus remains in the field.
    // Rebuilding on blur can otherwise detach the next control during focus transfer.
    const eventName=options.live||(input.tagName!=='SELECT'&&input.type!=='checkbox')?'input':'change';
    input.addEventListener(eventName,()=>{
        if(input.type==='number'&&input.value==='')return;
        const result=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;
        if(typeof result==='number'&&!Number.isFinite(result))return;change(result);
    });
    wrap.append(input);return wrap;
}
export function panelSection(title,{open=true}={}) {const root=el('details','lde-prop-section');root.open=open;root.append(el('summary','',title));const body=el('div','lde-prop-body');root.append(body);return{root,body};}
export function dialog(title,build,{wide=false,onClose}={}) {
    const panel=el('section','lde-dialog'+(wide?' lde-dialog-wide':'')),head=el('header','lde-dialog-head');head.append(el('h2','',title));panel.append(head);
    const body=el('div','lde-dialog-body'),foot=el('footer','lde-dialog-foot');panel.append(body,foot);
    const api=popovers.createPopover({panel,kind:'dialog',modal:true,label:title,onClose:()=>{onClose?.();setTimeout(()=>panel.remove(),140);}});
    head.append(button('关闭',()=>api.close(),'lde-icon-button'));build({body,foot,close:()=>api.close(),panel});api.open();return api;
}
export function downloadJson(document,name='学习文档草稿.json') {
    const url=URL.createObjectURL(new Blob([JSON.stringify(document,null,2)],{type:'application/json;charset=utf-8'})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function reportError(error,container) {
    container.hidden=false;container.replaceChildren(el('strong','',error.message||String(error)));
    const diagnostics=error.details?.diagnostics||[];
    if(diagnostics.length){const list=el('ul');for(const d of diagnostics.slice(0,12))list.append(el('li','',(d.path?d.path+'：':'')+(d.message||d.code||'内容需要检查')));container.append(list);}
}

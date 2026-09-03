import {createPopoverSystem} from '../ui_popover.js';
import {colorControl} from '../ui_color_picker.js';
export const popovers=createPopoverSystem();
export function el(tag, className='', text='') { const node=document.createElement(tag);if(className)node.className=className;if(text)node.textContent=text;return node; }
export function button(label, action, className='lde-button') { const b=el('button',className,label);b.type='button';b.addEventListener('click',action);return b; }
export function field(label,value,change,options={}) {
    if(options.type==='color'){
        const wrap=el('div','lde-field');wrap.append(el('span','lde-field-label',label),colorControl({label,value,onChange:change,onPreview:options.preview,popovers,mixed:options.mixed,allowReset:options.allowReset}));return wrap;
    }
    if(options.type==='number'&&options.min!=null&&options.max!=null&&!options.exact){
        const wrap=el('label','lde-field lde-range-field'),head=el('span','lde-range-head'),output=el('output'),input=el('input');
        head.append(el('span','lde-field-label',label),output);input.type='range';input.min=options.min;input.max=options.max;input.step=options.step??1;input.value=value??options.defaultValue??options.min;input.setAttribute('aria-label',label);
        const draw=()=>{const n=Number(input.value),text=options.format?options.format(n):String(n)+(options.unit||'');output.value=text;input.setAttribute('aria-valuetext',text);input.style.setProperty('--progress',((n-options.min)/(options.max-options.min||1))*100+'%');};draw();
        if(value==null)output.value=options.mixed?'混合值':options.defaultValue!=null?output.value:'默认';
        input.addEventListener('input',()=>{draw();options.preview?.(Number(input.value));});
        input.addEventListener('change',()=>change(Number(input.value)));wrap.append(head,input);return wrap;
    }
    if(options.visual&&options.choices){
        const wrap=el('div','lde-field'),choices=el('div','lde-visual-choices');choices.setAttribute('role','group');choices.setAttribute('aria-label',label);wrap.append(el('span','lde-field-label',label),choices);
        for(const [key,title]of Object.entries(options.choices)){
            const b=button('',()=>{for(const node of choices.children)node.setAttribute('aria-pressed',String(node===b));change(key);},'lde-choice');b.setAttribute('aria-label',title);b.setAttribute('aria-pressed',String(String(value)===key));b.append(choiceSample(options.visual,key),el('span','',title));choices.append(b);
        }return wrap;
    }
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
export function choiceSample(kind,key) {
    const sample=el('span','lde-choice-sample lde-sample-'+kind);sample.dataset.value=key;sample.setAttribute('aria-hidden','true');
    if(kind==='theme'){sample.style.backgroundColor={sky:'#0284c7',teal:'#0d9488',violet:'#7c3aed',amber:'#d97706',rose:'#e11d48',slate:'#475569'}[key];}
    else if(['font','weight','shadow'].includes(kind)){sample.textContent='文 Aa';if(kind==='weight')sample.style.fontWeight=key;}
    else if(kind==='size')sample.textContent='Aa';
    else if(kind==='variant')sample.textContent='按钮';
    else if(kind==='tone')sample.textContent='●';
    else if(kind==='ease'){
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),path=document.createElementNS(svg.namespaceURI,'path');svg.setAttribute('viewBox','0 0 60 32');path.setAttribute('d',{linear:'M4 28 L56 4',in:'M4 28 Q48 28 56 4',out:'M4 28 Q12 4 56 4',inout:'M4 28 C34 28 26 4 56 4'}[key]);path.setAttribute('fill','none');path.setAttribute('stroke','currentColor');path.setAttribute('stroke-width','2');svg.append(path);sample.append(svg);
    }else for(let i=0;i<(kind==='align'?3:4);i++)sample.append(el('i'));
    return sample;
}
export function panelSection(title,{open=true}={}) {const root=el('details','lde-prop-section');root.open=open;root.append(el('summary','',title));const body=el('div','lde-prop-body');root.append(body);return{root,body};}
export function dialog(title,build,{wide=false,onClose}={}) {
    const panel=el('section','lde-dialog'+(wide?' lde-dialog-wide':'')),head=el('header','lde-dialog-head');head.append(el('h2','',title));panel.append(head);
    const body=el('div','lde-dialog-body'),foot=el('footer','lde-dialog-foot');panel.append(body,foot);
    const api=popovers.createPopover({panel,kind:'dialog',modal:true,preserveOnResize:true,label:title,onClose:()=>{onClose?.();setTimeout(()=>panel.remove(),140);}});
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

import {dialog,el,button} from './lessondoc_editor/ui.js';
import {enhancePromptPoolInput,recordPromptForInput} from './prompt_pool.js';

export function ensureEditorStyles(){
    if(document.querySelector('link[href*="css/lessondoc_editor.css"]'))return;
    const link=document.createElement('link');link.rel='stylesheet';link.href='/static/css/lessondoc_editor.css';document.head.append(link);
}
export function openLessonDocPrompt({title,description,featureKey='lessondoc-slide-rewrite',submit,onSuccess}) {
    ensureEditorStyles();let pool,closed=false;
    return dialog(title,({body,foot,close})=>{
        body.append(el('p','lde-muted',description));
        const label=el('label','lde-field'),input=el('textarea');input.rows=5;input.maxLength=3000;input.dataset.promptPoolKey=featureKey;input.setAttribute('aria-label','改进要求');
        label.append(el('span','lde-field-label','改进要求'),input);body.append(label);pool=enhancePromptPoolInput(input);
        const error=el('div','lde-dialog-error');error.setAttribute('role','alert');body.append(error);
        const run=button('开始生成',async()=>{
            run.disabled=true;input.disabled=true;run.textContent='AI 正在编写…';error.textContent='';
            const hint=input.value;
            try{const result=await submit(hint);await recordPromptForInput(input,hint);if(closed)return;close();onSuccess?.(result);}
            catch(e){if(!closed)error.textContent=e.message||'生成失败，请重试。';}
            finally{run.disabled=false;input.disabled=false;run.textContent='开始生成';}
        },'lde-button lde-primary');foot.append(button('取消',close),run);
    },{onClose:()=>{closed=true;pool?.destroy();}});
}

import {clone,equal,setAt,at} from './model.js';
import {dialog,el,button,field,panelSection} from './ui.js';
import {gradientField,backgroundPreview,percent} from './appearance_controls.js';
import {openMedia} from './media_picker.js';

export function openBackground(store,api,config,onError,draft=null) {
    const home=store.model.kind==='home',index=store.ui.slide,serial=store.serial;
    const before=clone(store.model),paths=home?{home:['home','bg']}:{page:['slides',index,'bg'],deck:['bg']};
    let scope=draft?.scope||(home?'home':'page'),value=clone(draft?.value||at(store.model,paths[scope])||{}),active=true;
    const unchanged=()=>serial===store.serial&&index===store.ui.slide;
    const frame=document.getElementById('lde-frame');
    const resolveAsset=src=>{try{const url=new URL(src,frame.contentDocument.baseURI);return url.origin===location.origin?url.href:'';}catch{return '';}};
    dialog(home?'首页背景':'页面背景',({body,foot,close})=>{
        const controls=el('div','lde-background-controls');
        if(!home)body.append(field('应用范围',scope,v=>{scope=v;value=clone(at(store.model,paths[scope])||{});draw();},{choices:{page:'仅本页',deck:'全课默认背景'}}));body.append(controls);
        const draw=()=>{
            const settings=el('div','lde-background-settings');controls.replaceChildren(settings);
            const demo=backgroundPreview(home?store.model.course.name:store.model.slides[index].title||'学习文档',value,resolveAsset);
            const refresh=()=>demo.update(value);
            const preview=el('aside','lde-background-preview');preview.append(el('span','lde-field-label','当前背景效果'),demo.page);controls.append(preview);
            settings.append(field('底色',value.color,v=>{value.color=v;refresh();},{type:'color',preview:v=>demo.update({...value,color:v})}));
            const gradient=panelSection('渐变',{open:!!value.gradient});settings.append(gradient.root);
            gradient.body.append(gradientField('启用渐变',value.gradient,v=>{value.gradient=v;refresh();},{preview:v=>demo.update({...value,gradient:v})}));
            const image=panelSection('背景图片',{open:!!value.image});settings.append(image.root);
            image.body.append(button(value.image?'更换背景图片':'选择包内图片',()=>{
                close();openMedia(config,resource=>{
                    if(resource.kind!=='image'){onError(new Error('背景仅支持图片'));return;}
                    if(!unchanged()){onError(new Error('选择图片期间正文或当前页已变化，请重新设置背景。'));return;}
                    value.image={src:resource.src,fit:'cover',scale:100,x:50,y:50,opacity:1};
                    queueMicrotask(()=>openBackground(store,api,config,onError,{scope,value}));
                },onError);
            }));
            if(value.image){
                const thumb=el('img','lde-background-thumb');thumb.src=resolveAsset(value.image.src);thumb.alt='当前背景图片';image.body.append(thumb);
                image.body.append(field('填充方式',value.image.fit||'cover',v=>{value.image.fit=v;draw();},{choices:{cover:'铺满',contain:'完整',stretch:'拉伸',tile:'平铺',custom:'缩放'},visual:'fit'}));
                for(const[key,label,min,max,step,fallback,unit]of[['scale','缩放',10,400,1,100,'%'],['x','水平位置',0,100,1,50,'%'],['y','垂直位置',0,100,1,50,'%'],['rotate','旋转',-180,180,1,0,'°'],['opacity','不透明度',0,1,.01,1,''],['blur','模糊',0,40,1,0,' px']]){
                    if(key==='scale'&&value.image.fit!=='custom')continue;
                    image.body.append(field(label,value.image[key]??fallback,v=>{value.image[key]=v;refresh();},{type:'number',min,max,step,unit,format:key==='opacity'?percent:null,preview:v=>demo.update({...value,image:{...value.image,[key]:v}})}));
                }
                image.body.append(button('移除背景图片',()=>{delete value.image;draw();}));
            }
            const tint=panelSection('遮色',{open:!!value.tint});settings.append(tint.root);
            tint.body.append(field('启用遮色',!!value.tint,v=>{if(v)value.tint={color:'white',opacity:.3};else delete value.tint;draw();},{type:'checkbox'}));
            if(value.tint)tint.body.append(field('遮色颜色',value.tint.color,v=>{value.tint.color=v;refresh();},{type:'color',allowReset:false,preview:v=>demo.update({...value,tint:{...value.tint,color:v}})}),field('遮色强度',value.tint.opacity??.3,v=>{value.tint.opacity=v;refresh();},{type:'number',min:0,max:1,step:.01,format:percent,preview:v=>demo.update({...value,tint:{...value.tint,opacity:v}})}));
        };draw();
        const apply=button('应用背景',async()=>{
            if(apply.disabled)return;apply.disabled=true;
            try{
                if(!unchanged())throw new Error('正文或当前页已变化，请重新设置背景。');
                const snapshot=clone(value),target=scope,candidate=clone(before);setAt(candidate,paths[target],snapshot);
                const checked=await api.validate(candidate);if(!active)return;
                if(!checked.valid)throw new Error(checked.warnings.join('\n'));
                if(!unchanged()||target!==scope||!equal(value,snapshot))throw new Error('预检期间内容已变化，请再次应用。');
                store.command('设置背景',model=>setAt(model,paths[target],clone(at(checked.document,paths[target]))));close();
            }catch(error){onError(error);}finally{apply.disabled=false;}
        },'lde-button lde-primary');
        foot.append(button('恢复默认背景',()=>{if(!unchanged()){onError(new Error('正文或当前页已变化，请重新设置背景。'));return;}store.command('恢复默认背景',model=>setAt(model,paths[scope],undefined));close();}),apply);
    },{wide:true,onClose:()=>active=false});
}

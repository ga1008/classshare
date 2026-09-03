import {clone,equal,setAt,at} from './model.js';
import {dialog,el,button,field,panelSection} from './ui.js';
import {openMedia} from './media_picker.js';

export function openBackground(store,api,config,onError,draft=null) {
    const home=store.model.kind==='home',index=store.ui.slide;
    const before=clone(store.model),paths=home?{home:['home','bg']}:{page:['slides',index,'bg'],deck:['bg']};let scope=draft?.scope||(home?'home':'page'),value=clone(draft?.value||at(store.model,paths[scope])||{});
    dialog(home?'首页背景':'页面背景',({body,foot,close})=>{
        const controls=el('div');if(!home)body.append(field('应用范围',scope,v=>{scope=v;value=clone(at(store.model,paths[scope])||{});draw();},{choices:{page:'仅本页',deck:'全课默认背景'}}));body.append(controls);
        const draw=()=>{
            controls.replaceChildren();controls.append(field('底色',value.color||'',v=>value.color=v));
            const gradient=panelSection('渐变',{open:!!value.gradient});controls.append(gradient.root);
            for(const[key,label]of[['from','起始颜色'],['to','结束颜色'],['angle','角度']])gradient.body.append(field(label,value.gradient?.[key]??(key==='angle'?135:''),v=>{value.gradient??={};value.gradient[key]=v;},{type:key==='angle'?'number':'text',min:0,max:360}));
            gradient.body.append(button('移除渐变',()=>{delete value.gradient;draw();}));
            const image=panelSection('背景图片');controls.append(image.root);image.body.append(button('选择包内图片',()=>{
                close();openMedia(config,resource=>{if(resource.kind!=='image'){onError(new Error('背景仅支持图片'));return;}value.image={src:resource.src,fit:'cover',scale:100,x:50,y:50,opacity:1};
                    if(!equal(before,store.model)||index!==store.ui.slide){onError(new Error('选择图片期间正文已变化，请重新设置背景。'));return;}
                    queueMicrotask(()=>openBackground(store,api,config,onError,{scope,value}));
                },onError);
            }));
            if(value.image){image.body.append(el('p','lde-muted',value.image.src),field('填充方式',value.image.fit||'cover',v=>value.image.fit=v,{choices:{cover:'铺满',contain:'完整显示',stretch:'拉伸',tile:'平铺',custom:'自定义'}}));
                for(const[key,label,min,max,step]of[['scale','缩放 %',10,400,1],['x','水平位置 %',0,100,1],['y','垂直位置 %',0,100,1],['rotate','旋转角度',-180,180,1],['opacity','不透明度',0,1,.05],['blur','模糊',0,40,1]])image.body.append(field(label,value.image[key]??0,v=>value.image[key]=v,{type:'number',min,max,step}));image.body.append(button('移除背景图片',()=>{delete value.image;draw();}));}
            const tint=panelSection('遮色',{open:!!value.tint});controls.append(tint.root);tint.body.append(field('遮色颜色',value.tint?.color||'',v=>{value.tint??={opacity:.3};value.tint.color=v;}),field('遮色不透明度',value.tint?.opacity??.3,v=>{value.tint??={color:'white'};value.tint.opacity=v;},{type:'number',min:0,max:1,step:.05}));
        };draw();
        foot.append(button('恢复默认背景',()=>{store.command('恢复默认背景',model=>setAt(model,paths[scope],undefined));close();}),button('应用背景',async()=>{try{const candidate=clone(before);setAt(candidate,paths[scope],value);const checked=await api.validate(candidate);if(!checked.valid)throw new Error(checked.warnings.join('\n'));if(!equal(before,store.model))throw new Error('正文已变化，请重新设置背景。');store.command('设置背景',model=>setAt(model,paths[scope],clone(at(checked.document,paths[scope]))));close();}catch(e){onError(e);}},'lde-button lde-primary'));
    });
}

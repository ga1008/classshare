import {el,field,button} from './ui.js';
import {displayColor} from '../ui_color_picker.js';
import {clone} from './model.js';

export const percent=value=>Math.round(value*100)+'%';
export const seconds=value=>(value/1000).toFixed(2).replace(/0$/,'')+' 秒';
export const DEFAULT_GRADIENT={from:'primary',to:'primary-dark',angle:135};
// Matches the renderer's TEXT_BLOCKS: other block types use box shadows and backgrounds.
export const TEXT_STYLE_BLOCKS=new Set(['text','bigmark','bignum','cards','timeline','callout','quiz','reveal','button','tasklist','table','details','tabs']);
export function gradientCss(gradient) {
    return gradient?.from&&gradient?.to?'linear-gradient('+(gradient.angle??135)+'deg,'+displayColor(gradient.from)+','+displayColor(gradient.to)+')':'';
}
export function miniPage({text=false,title='学习文档',subtitle='内容与色彩，实时预览'}={}) {
    const page=el('div','lde-mini-page'),surface=el('div','lde-mini-surface'),content=el('div','lde-mini-content'),heading=el('strong','',title);
    content.append(heading,el('span','',subtitle));const cards=el('div','lde-mini-cards');cards.append(el('i'),el('i'),el('i'));content.append(cards,el('small','','01 / 03'));page.append(surface,content);page.setAttribute('role','img');page.setAttribute('aria-label',text?'文字渐变预览':'页面效果预览');
    return {page,surface,heading,update:gradient=>{const css=gradientCss(gradient);if(text){heading.style.backgroundImage=css;heading.classList.toggle('is-gradient',!!css);}else surface.style.backgroundImage=css;}};
}
/** Always writes a complete gradient, never an invalid single endpoint. */
export function gradientField(label,value,change,{text=false,preview}={}) {
    let gradient=value?{...DEFAULT_GRADIENT,...clone(value)}:null;
    const root=el('div','lde-gradient-control'),controls=el('div'),colors=el('div','lde-gradient-colors'),demo=miniPage({text});
    const draw=()=>{controls.hidden=!gradient;if(!gradient)return;colors.replaceChildren();
        for(const [key,label]of[['from','起始颜色'],['to','结束颜色']])colors.append(field(label,gradient[key],v=>{gradient={...gradient,[key]:v};demo.update(gradient);change(clone(gradient));preview?.(gradient);},{type:'color',allowReset:false,preview:v=>{const draft={...gradient,[key]:v};demo.update(draft);preview?.(draft);}}));
        controls.replaceChildren(colors,demo.page,field('渐变方向',gradient.angle,v=>{gradient.angle=v;change(clone(gradient));},{type:'number',min:0,max:360,unit:'°',preview:v=>{demo.update({...gradient,angle:v});preview?.({...gradient,angle:v});}}));demo.update(gradient);
    };
    root.append(field(label,!!gradient,v=>{gradient=v?{...DEFAULT_GRADIENT}:null;change(gradient?clone(gradient):undefined);draw();preview?.(gradient);},{type:'checkbox'}),controls);draw();return root;
}

export function backgroundPreview(title,bg,resolveAsset) {
    const demo=miniPage({title}),image=el('div','lde-mini-image'),tint=el('div','lde-mini-tint');demo.surface.append(image,tint);
    const update=value=>{
        demo.surface.style.backgroundColor=displayColor(value.color||'white');demo.surface.style.backgroundImage=gradientCss(value.gradient);
        const im=value.image;image.hidden=!im;
        if(im){
            const src=resolveAsset(im.src);image.style.backgroundImage=src?'url('+JSON.stringify(src)+')':'';
            image.style.backgroundSize={cover:'cover',contain:'contain',stretch:'100% 100%',tile:'auto'}[im.fit||'cover']||((im.scale??100)+'%');
            image.style.backgroundRepeat=im.fit==='tile'?'repeat':'no-repeat';image.style.backgroundPosition=(im.x??50)+'% '+(im.y??50)+'%';
            image.style.transform=im.rotate?'rotate('+im.rotate+'deg) scale(1.45)':'';image.style.opacity=im.opacity??1;image.style.filter=im.blur?'blur('+((im.blur||0)/6)+'px)':'';
        }
        tint.hidden=!value.tint;tint.style.backgroundColor=displayColor(value.tint?.color||'transparent');tint.style.opacity=value.tint?.opacity??.3;
    };update(bg);return {page:demo.page,update};
}

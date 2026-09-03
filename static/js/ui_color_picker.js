import {COLOR_TOKENS,DEFAULT_COLORS,normalizeColor,hexToHsv,hsvToHex,clamp,recordColor,commonColors,colorTextColor} from './ui_color_model.js';

let settings={userId:'local',resolveColor:color=>color};
export function configureColorPicker(options) { settings={...settings,...options}; }
export function displayColor(color) {
    if(color==='transparent')return '#00000000';if(color==='white')return '#ffffff';
    return normalizeColor(settings.resolveColor(color))||normalizeColor(color)||'#ffffff';
}
function node(tag,cls,text) {const n=document.createElement(tag);n.className=cls||'';if(text)n.textContent=text;return n;}
function action(text,fn,cls='ls-color-action') {const b=node('button',cls,text);b.type='button';b.addEventListener('click',fn);return b;}
function readUsage() {try{return JSON.parse(localStorage.getItem('lanshare:colors:v1:'+settings.userId)||'[]');}catch{return [];}}
function remember(color) {try{localStorage.setItem('lanshare:colors:v1:'+settings.userId,JSON.stringify(recordColor(readUsage(),color)));}catch{/* Picking still works when browser storage is unavailable. */}}
function swatchBackground(color) {const css=displayColor(color);return 'linear-gradient('+css+','+css+'),repeating-conic-gradient(#e2e8f0 0 25%,white 0 50%) 0 / 8px 8px';}

/** One shared palette; hosts supply their existing popover manager and change transaction. */
export function colorControl({label,value,onChange,onPreview,popovers,allowReset=true,mixed=false}) {
    let current=normalizeColor(value),popup=null;
    const trigger=action('',()=>open(),'ls-color-trigger');trigger.setAttribute('aria-label',label);trigger.setAttribute('aria-haspopup','dialog');
    const swatch=node('span','ls-color-swatch'),caption=node('span','ls-color-caption');trigger.append(swatch,caption,node('span','ls-color-chevron','⌄'));
    const paint=(color)=>{swatch.style.background=swatchBackground(color);trigger.style.background=color?swatchBackground(color):'';trigger.style.color=colorTextColor(displayColor(color));trigger.classList.toggle('is-default',!color);caption.textContent=color?(COLOR_TOKENS[color]||color.toUpperCase()):(mixed?'混合颜色':'默认');trigger.dataset.color=color;};
    paint(current);
    function open() {
        if(popup?.isOpen){popup.close('toggle');return;}
        let hsv=hexToHsv(displayColor(current||DEFAULT_COLORS[0])),draft=current,frame=0,pointer=null,rect=null;
        const panel=node('section','ls-color-picker'),heading=node('div','ls-color-heading');
        const close=action('完成',()=>popup.close(),'ls-color-done');heading.append(node('strong','',label),close);panel.append(heading);
        const plane=node('div','ls-color-plane'),cursor=node('span','ls-color-cursor');plane.append(cursor);plane.tabIndex=0;plane.setAttribute('role','group');plane.setAttribute('aria-label','色盘，左右调整饱和度，上下调整明度');plane.dataset.autofocus='';panel.append(plane);
        const slider=(label,max,step=1)=>{const wrap=node('label','ls-color-slider'),text=node('span','',label),input=node('input');input.type='range';input.min=0;input.max=max;input.step=step;wrap.append(text,input);panel.append(wrap);return input;};
        const hue=slider('色相',360),alpha=slider('不透明度',100);hue.className='ls-color-hue';alpha.className='ls-color-alpha';
        const row=node('div','ls-color-common');row.setAttribute('aria-label','常用颜色');panel.append(node('span','ls-color-label','常用颜色'),row);
        const drawCommon=()=>{const hadFocus=row.contains(document.activeElement);row.replaceChildren();for(const color of commonColors(readUsage())){
            const b=action('',()=>pick(color),'ls-color-chip');b.style.background=swatchBackground(color);b.setAttribute('aria-label','常用颜色 '+(COLOR_TOKENS[color]||color));b.title=COLOR_TOKENS[color]||color;b.setAttribute('aria-pressed',String(color===current));row.append(b);
        }if(hadFocus)(row.querySelector('[aria-pressed="true"]')||plane).focus({preventScroll:true});};
        const theme=node('details','ls-color-theme');theme.append(node('summary','','跟随文档主题'));const tokens=node('div','ls-color-tokens');theme.append(tokens);panel.append(theme);
        for(const [color,name]of Object.entries(COLOR_TOKENS)){const b=action(name,()=>pick(color),'ls-color-token');b.style.setProperty('--color',displayColor(color));tokens.append(b);}
        const exact=node('label','ls-color-exact');exact.append(node('span','','色值'));const hex=node('input');hex.type='text';hex.spellcheck=false;hex.maxLength=24;hex.setAttribute('aria-label','精确色值');exact.append(hex);panel.append(exact);
        const message=node('span','ls-color-error');message.setAttribute('role','status');panel.append(message);
        if(allowReset)panel.append(action('恢复默认颜色',()=>{current='';paint(current);onChange(undefined);popup.close('reset');}));
        function draw() {
            plane.style.backgroundColor='hsl('+hsv.h+' 100% 50%)';cursor.style.left=hsv.s*100+'%';cursor.style.top=(1-hsv.v)*100+'%';cursor.style.backgroundColor=displayColor(draft);
            hue.value=hsv.h;alpha.value=Math.round(hsv.a*100);alpha.style.setProperty('--color',hsvToHex({...hsv,a:1}));hex.value=draft||hsvToHex(hsv);
            plane.setAttribute('aria-description','饱和度 '+Math.round(hsv.s*100)+'%，明度 '+Math.round(hsv.v*100)+'%');
            hue.setAttribute('aria-valuetext',Math.round(hsv.h)+'°');alpha.setAttribute('aria-valuetext',Math.round(hsv.a*100)+'%');
        }
        function preview() {draft=hsvToHex(hsv);paint(draft);draw();onPreview?.(draft);}
        function queuePreview() {if(!frame)frame=requestAnimationFrame(()=>{frame=0;preview();});}
        function commit() {if(frame){cancelAnimationFrame(frame);frame=0;}preview();current=draft;onChange(current);remember(current);drawCommon();}
        function pick(color) {current=draft=color;hsv=hexToHsv(displayColor(color));paint(color);draw();onPreview?.(color);onChange(color);remember(color);drawCommon();}
        const point=event=>{hsv.s=clamp((event.clientX-rect.left)/rect.width);hsv.v=1-clamp((event.clientY-rect.top)/rect.height);queuePreview();};
        plane.addEventListener('pointerdown',event=>{if(event.button!==0||pointer!==null)return;event.preventDefault();plane.focus();pointer=event.pointerId;rect=plane.getBoundingClientRect();plane.setPointerCapture(pointer);point(event);});
        plane.addEventListener('pointermove',event=>{if(event.pointerId===pointer)point(event);});
        plane.addEventListener('pointerup',event=>{if(event.pointerId!==pointer)return;point(event);pointer=null;commit();});
        plane.addEventListener('pointercancel',()=>{pointer=null;if(frame)cancelAnimationFrame(frame);frame=0;draft=current;hsv=hexToHsv(displayColor(current));paint(current);draw();onPreview?.(current);});
        plane.addEventListener('keydown',event=>{const step=event.shiftKey?.1:.01;if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();event.stopPropagation();if(event.key==='ArrowLeft')hsv.s=clamp(hsv.s-step);if(event.key==='ArrowRight')hsv.s=clamp(hsv.s+step);if(event.key==='ArrowUp')hsv.v=clamp(hsv.v+step);if(event.key==='ArrowDown')hsv.v=clamp(hsv.v-step);preview();});
        plane.addEventListener('keyup',event=>{if(event.key.startsWith('Arrow')){event.stopPropagation();commit();}});
        for(const [input,key,factor]of[[hue,'h',1],[alpha,'a',100]]){input.addEventListener('input',()=>{hsv[key]=Number(input.value)/factor;queuePreview();});input.addEventListener('change',commit);}
        hex.addEventListener('change',()=>{const value=normalizeColor(hex.value);message.textContent=value?'':'请输入有效的 HEX 色值或主题色名称';if(value)pick(value);});
        hex.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();hex.dispatchEvent(new Event('change'));}});
        popup=popovers.createPopover({panel,anchor:trigger,parent:'anchor',label:label+'色盘',onClose:()=>{if(frame)cancelAnimationFrame(frame);paint(current);onPreview?.(current);setTimeout(()=>panel.remove(),140);}});
        draw();drawCommon();popup.open();
    }
    return trigger;
}

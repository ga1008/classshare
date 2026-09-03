/** Serializable colors match the document contract: hex (including alpha) or tokens. */
export const COLOR_TOKENS = {primary:'主题色','primary-dark':'深主题色','primary-soft':'浅主题色',text:'正文',muted:'次要文字',ok:'成功',warn:'提醒',err:'错误',white:'白色',transparent:'透明'};
export const DEFAULT_COLORS = ['#0284c7','#0d9488','#7c3aed','#e11d48','#d97706','#16a34a','#1e293b','#64748b','#ffffff','#000000'];
export const clamp = (n,min=0,max=1) => Math.min(max,Math.max(min,n));
export function normalizeColor(value) {
    const color=String(value??'').trim().toLowerCase();
    if(Object.hasOwn(COLOR_TOKENS,color))return color;
    if(!/^#(?:[\da-f]{3}|[\da-f]{4}|[\da-f]{6}|[\da-f]{8})$/.test(color))return '';
    let hex=color.slice(1);if(hex.length<=4)hex=[...hex].map(c=>c+c).join('');
    if(hex.length===8&&hex.endsWith('ff'))hex=hex.slice(0,6);
    return '#'+hex;
}
export function hexToHsv(hex) {
    const value=normalizeColor(hex);if(!value.startsWith('#'))return {h:200,s:1,v:.78,a:1};
    const [r,g,b]=[1,3,5].map(i=>parseInt(value.slice(i,i+2),16)/255),max=Math.max(r,g,b),min=Math.min(r,g,b),d=max-min;
    let h=0;if(d)h=(max===r?(g-b)/d+(g<b?6:0):max===g?(b-r)/d+2:(r-g)/d+4)*60;
    return {h,s:max?d/max:0,v:max,a:value.length===9?parseInt(value.slice(7),16)/255:1};
}
export function hsvToHex({h,s,v,a=1}) {
    h=((h%360)+360)%360;s=clamp(s);v=clamp(v);a=clamp(a);
    const c=v*s,x=c*(1-Math.abs(h/60%2-1)),m=v-c;
    const rgb=h<60?[c,x,0]:h<120?[x,c,0]:h<180?[0,c,x]:h<240?[0,x,c]:h<300?[x,0,c]:[c,0,x];
    const byte=n=>Math.round(n*255).toString(16).padStart(2,'0');
    return '#'+rgb.map(n=>byte(n+m)).join('')+(Math.round(a*255)<255?byte(a):'');
}
export function colorTextColor(color) {
    const hex=normalizeColor(color);if(!hex.startsWith('#'))return '#1e293b';
    const alpha=hex.length===9?parseInt(hex.slice(7),16)/255:1;
    const channels=[1,3,5].map(i=>{const c=(parseInt(hex.slice(i,i+2),16)*alpha+255*(1-alpha))/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;});
    return channels.reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0)>.179?'#000000':'#ffffff';
}
export function rankedColors(raw,limit=48) {
    if(!Array.isArray(raw))return [];
    const merged=new Map();
    for(const entry of raw.slice(0,256)){
        const color=normalizeColor(entry?.color);if(!color||!Number.isFinite(entry.count)||entry.count<1)continue;
        const item={color,count:Math.min(1e6,Math.floor(entry.count)),last:Number.isFinite(entry.last)?entry.last:0};
        const prior=merged.get(color);if(!prior||item.count>prior.count)merged.set(color,item);
    }
    return [...merged.values()].sort((a,b)=>b.count-a.count||b.last-a.last||a.color.localeCompare(b.color)).slice(0,limit);
}
export function recordColor(raw,color,now=Date.now()) {
    color=normalizeColor(color);const entries=rankedColors(raw);if(!color)return entries;
    const prior=entries.find(item=>item.color===color);
    return rankedColors([...entries.filter(item=>item.color!==color),{color,count:(prior?.count||0)+1,last:now}]);
}
export function commonColors(raw,limit=10) {
    return [...new Set([...rankedColors(raw).map(item=>item.color),...DEFAULT_COLORS])].slice(0,limit);
}

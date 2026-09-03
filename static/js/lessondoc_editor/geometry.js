export const identity = () => [1, 0, 0, 1, 0, 0];
export function multiply(a, b) {
    return [a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1], a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3], a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5]];
}
export const transform = (m, p) => ({ x: m[0]*p.x+m[2]*p.y+m[4], y: m[1]*p.x+m[3]*p.y+m[5] });
export function inverse(m) {
    const d = m[0]*m[3]-m[1]*m[2]; if (Math.abs(d) < 1e-9) throw new Error('对象缩放比例无效');
    return [m[3]/d, -m[1]/d, -m[2]/d, m[0]/d, (m[2]*m[5]-m[3]*m[4])/d, (m[1]*m[4]-m[0]*m[5])/d];
}
export function frameMatrix(f) {
    const r = (f.r || 0) * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    return [c, s, -s, c, f.x+f.w/2-c*f.w/2+s*f.h/2, f.y+f.h/2-s*f.w/2-c*f.h/2];
}
export function parentMatrix(ancestors) {
    return ancestors.filter((b) => b.type === 'group').reduce((m, b) => {
        const f = b.frame, n = b.natural || f;
        return multiply(m, multiply(frameMatrix(f), [f.w/n.w, 0, 0, f.h/n.h, 0, 0]));
    }, identity());
}
export function corners(frame, parent = identity()) {
    const m = multiply(parent, frameMatrix(frame));
    return [{x:0,y:0},{x:frame.w,y:0},{x:frame.w,y:frame.h},{x:0,y:frame.h}].map((p) => transform(m,p));
}
export function bounds(points) {
    const xs=points.map((p)=>p.x), ys=points.map((p)=>p.y), x=Math.min(...xs), y=Math.min(...ys);
    return {x, y, w:Math.max(...xs)-x, h:Math.max(...ys)-y};
}
export const overlaps = (a,b) => a.x <= b.x+b.w && b.x <= a.x+a.w && a.y <= b.y+b.h && b.y <= a.y+a.h;
export function movedFrame(frame, delta, ancestors = [], { snap = true } = {}) {
    const inv = inverse(parentMatrix(ancestors)), zero = transform(inv,{x:0,y:0}), end=transform(inv,delta);
    const x=frame.x+end.x-zero.x, y=frame.y+end.y-zero.y;
    return {...frame,x:Math.max(-200,Math.min(1480,snap?Math.round(x/8)*8:x)),y:Math.max(-200,Math.min(920,snap?Math.round(y/8)*8:y))};
}
// Resize keeps the opposite corner fixed even for rotated frames.
export function resizedFrame(frame, point, ancestors = [], { uniform = false, center = false } = {}) {
    const p = transform(inverse(multiply(parentMatrix(ancestors), frameMatrix(frame))), point);
    let w = Math.max(8, Math.min(1680, p.x)), h = Math.max(8, Math.min(1680, p.y));
    if (uniform) { const scale = Math.max(8/frame.w,8/frame.h,Math.min(1680/frame.w,1680/frame.h,Math.max(w/frame.w,h/frame.h))); w=frame.w*scale; h=frame.h*scale; }
    if (center) return {...frame,w,h,x:frame.x+(frame.w-w)/2,y:frame.y+(frame.h-h)/2};
    const origin=transform(frameMatrix(frame),{x:0,y:0}), r=(frame.r||0)*Math.PI/180, c=Math.cos(r),s=Math.sin(r);
    return {...frame,w,h,x:origin.x-w/2+c*w/2-s*h/2,y:origin.y-h/2+s*w/2+c*h/2};
}

// Stop the whole gesture at the first legal boundary. Applying one progress
// fraction preserves spacing even when selected objects have rotated parents.
export function movedSelection(items, delta) {
    const changes=items.filter(item=>item.block.frame).map(item=>{
        const inv=inverse(parentMatrix(item.ancestors||[])),zero=transform(inv,{x:0,y:0}),end=transform(inv,delta);
        return {item,dx:end.x-zero.x,dy:end.y-zero.y};
    });
    let progress=1;
    for(const {item,dx,dy} of changes)for(const [key,d,min,max] of [['x',dx,-200,1480],['y',dy,-200,920]]){
        if(d>0)progress=Math.min(progress,Math.max(0,(max-item.block.frame[key])/d));
        if(d<0)progress=Math.min(progress,Math.max(0,(min-item.block.frame[key])/d));
    }
    return new Map(changes.map(({item,dx,dy})=>[item.block.id,{...item.block.frame,x:item.block.frame.x+dx*progress,y:item.block.frame.y+dy*progress}]));
}

export function offsetPastedElements(elements) {
    const positioned=elements.filter(b=>b.frame);if(!positioned.length)return;
    const box=bounds(positioned.flatMap(b=>corners(b.frame)));
    const dx=box.w<=1280?Math.max(-box.x,Math.min(16,1280-box.x-box.w)):-box.x;
    const dy=box.h<=720?Math.max(-box.y,Math.min(16,720-box.y-box.h)):-box.y;
    const frames=movedSelection(positioned.map(block=>({block})),{x:dx,y:dy});
    for(const block of positioned)block.frame=frames.get(block.id);
}

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
export function resizedFrame(frame, point, ancestors = [], { uniform = false, center = false, handle = 'se' } = {}) {
    const p = transform(inverse(multiply(parentMatrix(ancestors), frameMatrix(frame))), point);
    const sx=handle.includes('e')?1:handle.includes('w')?-1:0,sy=handle.includes('s')?1:handle.includes('n')?-1:0;
    const ax=center?.5:sx===1?0:sx===-1?1:.5,ay=center?.5:sy===1?0:sy===-1?1:.5;
    const anchor={x:frame.w*ax,y:frame.h*ay},factor=center?2:1;
    let w=sx?(p.x-anchor.x)*sx*factor:frame.w,h=sy?(p.y-anchor.y)*sy*factor:frame.h;
    if(uniform){
        const vx=sx*frame.w/factor,vy=sy*frame.h/factor;
        const scale=Math.max(8/frame.w,8/frame.h,Math.min(1680/frame.w,1680/frame.h,((p.x-anchor.x)*vx+(p.y-anchor.y)*vy)/(vx*vx+vy*vy)));
        w=frame.w*scale;h=frame.h*scale;
    }else{w=Math.max(8,Math.min(1680,w));h=Math.max(8,Math.min(1680,h));}
    const fixed=transform(frameMatrix(frame),anchor),r=(frame.r||0)*Math.PI/180,c=Math.cos(r),s=Math.sin(r);
    const target={...frame,w,h,x:fixed.x-w/2-c*(ax-.5)*w+s*(ay-.5)*h,y:fixed.y-h/2-s*(ax-.5)*w-c*(ay-.5)*h};
    // Interpolate to the first position limit, preserving the anchor and ratio.
    let progress=1;
    for(const [key,min,max]of[['x',-200,1480],['y',-200,920]]){const delta=target[key]-frame[key];if(delta>0)progress=Math.min(progress,(max-frame[key])/delta);if(delta<0)progress=Math.min(progress,(min-frame[key])/delta);}
    progress=Math.max(0,progress);for(const key of ['x','y','w','h'])target[key]=frame[key]+(target[key]-frame[key])*progress;
    return target;
}

export const RESIZE_HANDLES={nw:[0,0],n:[.5,0],ne:[1,0],e:[1,.5],se:[1,1],s:[.5,1],sw:[0,1],w:[0,.5]};
export function resizeCursor(handle,matrix=identity()) {
    const [x,y]=RESIZE_HANDLES[handle],v={x:x-.5,y:y-.5};
    const angle=Math.atan2(matrix[1]*v.x+matrix[3]*v.y,matrix[0]*v.x+matrix[2]*v.y)*180/Math.PI;
    return ['ew-resize','nwse-resize','ns-resize','nesw-resize'][((Math.round(angle/45)%4)+4)%4];
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

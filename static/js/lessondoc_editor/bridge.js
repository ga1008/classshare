import { clone, equal } from './model.js';

export class EditorBridge {
    constructor(frame, store, onError) { this.frame=frame;this.store=store;this.onError=onError;this.api=null;this.prior=null;this.controller=null;this.destroyed=false; }
    async connect() {
        const started=Date.now();
        while(!this.destroyed&&Date.now()-started<15000) {
            const win=this.frame.contentWindow;
            if(win?.LESSONDOC?.edit&&win.LESSONDOC.__engine&&win.document.readyState==='complete') {
                this.window=win;this.document=win.document;this.api=win.LESSONDOC.edit;
                this.api.mount({slide:this.store.ui.slide});this.render(true);
                this.blockNavigation=(e)=>{if(e.target.closest?.('a[href]'))e.preventDefault();};
                this.document.addEventListener('click',this.blockNavigation,true);
                this.unsubscribe=this.store.subscribe((event)=>{
                    if(['document','saved'].includes(event.type))this.render();
                    if(event.type==='page'){this.window.SLIDES?.goTo(this.store.ui.slide);this.api.select([]);this.controller?.draw();}
                    if(event.type==='selection'){this.api.select(this.store.ui.selection);this.controller?.draw();}
                });
                this.onGeometry=()=>this.controller?.draw();this.api.on('geometry',this.onGeometry);
                return this;
            }
            await new Promise(resolve=>setTimeout(resolve,80));
        }
        throw new Error('预览加载失败，请刷新页面或检查登录状态。');
    }
    render(force=false) {
        if(!this.api)return;
        const model=this.store.model,index=this.store.ui.slide;
        try {
            const oldRoot=this.prior?{...this.prior,slides:null}:null,newRoot={...model,slides:null};
            const samePages=this.prior?.slides?.length===model.slides?.length&&model.slides?.every((s,i)=>s.id===this.prior.slides[i].id);
            if(!force&&model.kind!=='home'&&samePages&&equal(oldRoot,newRoot)) {
                model.slides.forEach((slide,i)=>{if(!equal(slide,this.prior.slides[i]))this.api.patchSlide(clone(slide),i);});
            } else this.api.render(clone(model),index);
            this.prior=clone(model);this.api.select(this.store.ui.selection);this.controller?.draw();
        } catch(error){this.onError(error);}
    }
    trial(enabled) {
        this.store.ui.trial=enabled;
        // Reset runtime movement, visibility and players from JSON on both boundaries.
        this.render(true);this.api.previewActions(enabled);this.controller?.draw();
    }
    node(id) { return this.api.slideEl()?.querySelector('[data-ld-id="'+id+'"],[data-ld-gid="'+id+'"]'); }
    point(event) { return this.api.toCanvas(event.clientX,event.clientY); }
    destroy() {
        this.destroyed=true;this.unsubscribe?.();this.controller?.destroy();this.document?.removeEventListener('click',this.blockNavigation,true);
        this.api?.off('geometry',this.onGeometry);this.api?.unmount();
    }
}

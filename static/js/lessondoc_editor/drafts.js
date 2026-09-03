import { equal } from './model.js';
export class Drafts {
    constructor(user, pack, lesson, storage = window.localStorage) { this.key='lanshare:lessondoc:draft:'+user+':'+pack+':'+lesson; this.storage=storage; this.timer=null; }
    read() {
        try { const d=JSON.parse(this.storage.getItem(this.key)||'null'); return d?.format===1 && d.document && typeof d.revision==='string' ? d : null; } catch { return null; }
    }
    write(store) {
        if (!store.dirty) { this.storage.removeItem(this.key); return; }
        this.storage.setItem(this.key,JSON.stringify({format:1,updatedAt:Date.now(),revision:store.revision,document:store.model}));
    }
    schedule(store,onError) { clearTimeout(this.timer); this.timer=setTimeout(()=>{try{this.write(store);}catch(error){onError(error);}},10000); }
    candidate(loaded) { const d=this.read(); return d&&!equal(d.document,loaded.document)?{...d,conflict:d.revision!==loaded.revision}:null; }
    remove() { clearTimeout(this.timer); this.storage.removeItem(this.key); }
    destroy() { clearTimeout(this.timer); }
}

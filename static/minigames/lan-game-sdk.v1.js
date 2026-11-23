/* LanShare Game SDK v1. Distribute this classic script verbatim in each package. */
(() => {
  "use strict";
  const args = new URLSearchParams(location.hash.slice(1));
  const expectedHost = args.get("ls_host"), nonce = args.get("ls_nonce");
  const offline = window.parent === window && location.protocol === "file:" && !expectedHost && !nonce;
  const fork = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  const deepFreeze = value => {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      Object.values(value).forEach(deepFreeze);
      Object.freeze(value);
    }
    return value;
  };
  const id = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2,"0")).join("");
  const fault = code => Object.assign(new Error(code), {code});
  const listeners = new Map(), pending = new Map(), inflight = new Set();
  let port, ctx, def, sim, flush, privateRevision = 0, paused = false, closed = false, timer;
  let readyResolve, readyReject;
  const ready = new Promise((res,rej) => {readyResolve=res; readyReject=rej;});
  ready.catch(() => {});
  function emit(event, data) {
    for (const fn of listeners.get(event) || []) {
      try { fn(fork(data)); } catch (e) { console.error(e); }
    }
  }
  function welcome(context, definition) {
    ctx = fork(context);
    def = deepFreeze(fork(definition));
    if (ctx.environment === "preview" || ctx.environment === "local") sim = simulator(ctx.environment === "local");
    clearTimeout(timer);
    readyResolve(fork(ctx));
  }
  async function receive({data:m}) {
    if (!m || closed) return;
    if (!ctx && m.type === "welcome") return welcome(m.context, m.definition);
    if (m.v !== 1 || !ctx) return;
    if (m.event) {
      if (m.event === "session.resumeInput") paused = false;
      if (m.event === "audio.changed") ctx.audio = fork(m.data);
      if (m.event === "session.changed" && m.data?.me) ctx = {...ctx, ...fork(m.data)};
      emit(m.event,m.data);
    } else if (m.method === "prepareLeave") {
      paused = true;
      try {
        await Promise.all([...inflight]);
        const save = flush ? await flush() : null;
        port.postMessage({v:1,id:m.id,ok:true,result:{transitionId:m.params?.transitionId,privateSave:save ?? null,
          expectedPrivateRevision:privateRevision,expectedRevision:privateRevision}});
      } catch (e) {
        paused = false;
        port?.postMessage({v:1,id:m.id,ok:false,error:{code:e.code || "SAVE_FAILED"}});
      }
    } else if (pending.has(m.id)) {
      const item = pending.get(m.id);
      clearTimeout(item.timer); pending.delete(m.id);
      if (m.ok) item.resolve(m.result); else item.reject(fault(m.error?.code || m.error?.detail?.code || "REQUEST_FAILED"));
    }
  }
  function discover(e) {
    if (closed || port || !nonce || !expectedHost || e.source !== parent || e.origin !== expectedHost || e.data?.type !== "lanshare:discover") return;
    const channel = new MessageChannel(); port = channel.port1;
    port.onmessage = receive;
    parent.postMessage({type:"lanshare:hello",protocol:1,nonce}, expectedHost, [channel.port2]);
  }
  function dispose() {
    closed = true;
    clearTimeout(timer);
    readyReject(fault("DISCONNECTED"));
    window.removeEventListener("message",discover);
    port?.close();
    for (const item of pending.values()) {clearTimeout(item.timer);item.reject(fault("DISCONNECTED"));}
    pending.clear(); listeners.clear(); flush = null;
  }
  if (offline) {
    const start = () => {
      const d = window.LanGameOfflineContracts;
      if (!d?.config || !d.writeback) return readyReject(fault("MISSING_OFFLINE_CONTRACT"));
      welcome({protocol:1, environment:"local", mode:d.config.standalone==="solo"?"solo":"localSeats", capabilities:d.config.capabilities,
        me:{playerId:"local-1",displayName:"本机玩家",seat:1},runId:id(),roomId:null,audio:{muted:false}}, d);
    };
    if (!window.LanGameOfflineContracts && document.readyState!=="complete") document.addEventListener("DOMContentLoaded",start,{once:true}); else queueMicrotask(start);
  } else {
    window.addEventListener("message",discover);
    timer=setTimeout(()=>readyReject(fault("HOST_UNAVAILABLE")),15000);
  }
  window.addEventListener("pagehide",dispose,{once:true});

  async function call(method,params={}) {
    await ready;
    if (closed) throw fault("DISCONNECTED");
    if (sim) return sim.call(method,params);
    if (pending.size >= 32) throw fault("BUSY");
    const key=id();
    return new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>{pending.delete(key);reject(fault("RESULT_UNKNOWN"));},10000);
      pending.set(key,{resolve,reject,timer:timeout});
      port.postMessage({v:1,id:key,method,params});
    });
  }
  function write(method,params) {
    if (paused) return Promise.reject(fault("TRANSITION_PENDING"));
    const promise=call(method,params);
    inflight.add(promise);
    promise.then(()=>inflight.delete(promise),()=>inflight.delete(promise));
    return promise;
  }
  function simulator(persistent) {
    const {config,writeback:w,rules}=def;
    const profile="default", storageKey=["lanshare.game.v1",profile,config.gameKey,w.series,w.saveVersion].join("/");
    const defaults=Object.fromEntries(Object.entries(w.privateFields).map(([key,spec])=>[key,fork(spec.default)]));
    let save={rank:0,privateSave:defaults,privateRevision:0,history:[]};
    function shape(value,spec) {
      switch(spec.type){
        case "boolean":return typeof value==="boolean";
        case "integer":return Number.isInteger(value)&&value>=(spec.min??spec.minimum)&&value<=(spec.max??spec.maximum);
        case "enum":return spec.values.includes(value);
        case "string":return typeof value==="string"&&value.length>=(spec.minLength||0)&&value.length<=spec.maxLength;
        case "document":return shape(value,spec.schema);
        case "array":return Array.isArray(value)&&value.length<=spec.maxItems&&value.every(v=>shape(v,spec.items));
        case "object":return !!value&&typeof value==="object"&&!Array.isArray(value)&&spec.required.every(k=>Object.hasOwn(value,k))&&Object.keys(value).every(k=>Object.hasOwn(spec.properties,k)&&shape(value[k],spec.properties[k]));
        default:return false;
      }
    }
    function validPrivate(value) {
      return value && typeof value==="object" && Object.keys(value).length===Object.keys(w.privateFields).length &&
        Object.entries(w.privateFields).every(([k,s])=>Object.hasOwn(value,k)&&shape(value[k],s)) &&
        new TextEncoder().encode(JSON.stringify(value)).length<=32768;
    }
    function importData(data) {
      if (!data || data.key!==storageKey || !data.save || !Number.isInteger(data.save.rank) || data.save.rank<0 ||
          data.save.rank>=w.milestones.length || !validPrivate(data.save.privateSave) || !Number.isSafeInteger(data.save.privateRevision) ||
          data.save.privateRevision<0 || !Array.isArray(data.save.history) || data.save.history.length>64) throw fault("INVALID_LOCAL_SAVE");
      save=fork(data.save);
    }
    if(persistent) try {const raw=localStorage.getItem(storageKey); if(raw)importData(JSON.parse(raw));} catch {}
    const persist=()=>{if(persistent)try{localStorage.setItem(storageKey,JSON.stringify({key:storageKey,save}));}catch{}};
    let startRank=save.rank, revision=1, seq=1, outcome=null;
    let state={rank:startRank,turnSeat:1,node:w.milestones[startRank].resumeState};
    if(rules?.engine==="grid-line-v1") state={rank:0,turnSeat:1,board:Array(rules.size**2).fill(0),moves:0};
    const size=ctx.mode==="localSeats"||(!config.modes.solo&&ctx.environment==="preview")?config.modes.multiplayer.minPlayers:1;
    const members=Array.from({length:size},(_,n)=>({seat:n+1,playerId:"local-"+(n+1),displayName:size===1?ctx.me.displayName:"玩家 "+(n+1),status:"active"}));
    const receipts=new Map(), started=Date.now();
    const snapshot=()=>({phase:outcome?"ended":"running",mode:ctx.mode,startRank,seat:state.turnSeat,turnSeat:state.turnSeat,
      state:fork(state),outcome,members:fork(members),revision,seq,serverNow:Date.now(),startsAt:started});
    const equal=(a,b)=>typeof a===typeof b&&(a && typeof a==="object"?b!==null&&Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>Object.hasOwn(b,k)&&equal(a[k],b[k])):a===b);
    function move(action) {
      if (!rules) throw fault("NOT_APPLICABLE");
      if(rules.engine==="grid-line-v1"){
        const {x,y}=action.payload||{}, n=rules.size, i=y*n+x;
        if(action.type!=="place"||!Number.isInteger(x)||!Number.isInteger(y)||x<0||y<0||x>=n||y>=n||state.board[i])throw fault("ILLEGAL_MOVE");
        const seat=state.turnSeat; state.board[i]=seat; state.moves++;
        for(const [dx,dy] of [[1,0],[0,1],[1,1],[1,-1]]){
          let len=1;
          for(const sign of [-1,1]){let a=x+sign*dx,b=y+sign*dy;
            while(a>=0&&a<n&&b>=0&&b<n&&state.board[b*n+a]===seat){len++;a+=sign*dx;b+=sign*dy;}}
          if(len>=rules.line){state.winnerSeat=seat;outcome="actorWins";break;}
        }
        if(!outcome&&state.moves===n*n)outcome="draw";
        if(!outcome)state.turnSeat=3-seat;
      }else{
        const edge=rules.transitions.find(t=>t.from===state.node&&t.type===action.type&&equal(t.payload,action.payload));
        if(!edge)throw fault("ILLEGAL_MOVE");
        state.node=edge.to; outcome=rules.states[edge.to].outcome;
        if(outcome==="actorWins")state.winnerSeat=state.turnSeat;
        if(edge.turn==="next")state.turnSeat=state.turnSeat%size+1;
        if(w.milestones[state.rank+1]?.resumeState===edge.to)state.rank++;
        save.rank=Math.max(save.rank,state.rank);
      }
    }
    return {call(method,p){
      if(method==="session.snapshot")return snapshot();
      if(method==="progress.read")return {series:w.series,saveVersion:w.saveVersion,rank:save.rank,milestoneId:w.milestones[save.rank].id,privateSave:fork(save.privateSave),privateRevision:save.privateRevision};
      if(method==="room.members")return fork(members);
      if(method==="chat.open")return {opened:false};
      if(method==="history.list"){const offset=Math.max(0,Number(p.cursor)||0), limit=Math.max(1,Math.min(20,Number(p.limit)||20));return {items:fork(save.history.slice(offset,offset+limit)),nextCursor:offset+limit<save.history.length?String(offset+limit):null};}
      const signature=JSON.stringify([method,p]);
      if(!p.requestId)throw fault("REQUEST_ID_REQUIRED");
      const previous=receipts.get(p.requestId);
      if(previous){if(previous.signature!==signature)throw fault("KEY_CONFLICT");return fork(previous.result);}
      let result;
      if(method==="progress.writePrivate"){
        if(!ctx.capabilities.includes("save.private")||!validPrivate(p.values))throw fault("INVALID_PRIVATE_SAVE");
        if(p.expectedRevision!==save.privateRevision)throw fault("STALE_REVISION");
        save.privateSave=fork(p.values);save.privateRevision++;result={privateRevision:save.privateRevision};
        emit("progress.changed",result);
      }else{
        if(outcome)throw fault("SESSION_ENDED");
        if(method==="game.intent"){
          if(p.expectedRevision!==revision)throw fault("STALE_REVISION");
          move(p.action);
        }else if(method==="game.finish"&&!rules)outcome="completed";
        else throw fault("NOT_APPLICABLE");
        revision++;seq++;result={runId:ctx.runId,revision,throughSeq:seq};
        if(outcome){save.history.unshift({startedAt:started,endedAt:Date.now(),mode:ctx.mode,outcome,rank:save.rank});save.history.length=Math.min(save.history.length,64);}
        emit("game.changed",result);if(outcome)emit("session.ended",{reason:outcome});
      }
      receipts.set(p.requestId,{signature,result});if(receipts.size>256)receipts.delete(receipts.keys().next().value);
      persist();return result;
    }, exportData:()=>fork({key:storageKey,save}), importData:data=>{importData(data);persist();}};
  }
  const sdk={ready:async()=>{await ready;return fork(ctx);},definition:async()=>{await ready;return def;},newRequestId:id,
    session:{snapshot:()=>call("session.snapshot")},
    game:{intent:(action,opts={})=>write("game.intent",{action, ...opts}),finish:(opts={})=>write("game.finish",opts)},
    progress:{read:async()=>{const r=await call("progress.read");privateRevision=r.privateRevision;return r;},
      writePrivate:async(values,opts={})=>{const r=await write("progress.writePrivate",{values,...opts});privateRevision=r.privateRevision;return r;},
      onFlush:handler=>{flush=handler;return ()=>{if(flush===handler)flush=null;};}},
    history:{list:(params={})=>call("history.list",params)},room:{members:()=>call("room.members")},chat:{open:()=>call("chat.open")},
    local:{export:()=>{if(!offline||!sim)throw fault("NOT_APPLICABLE");return sim.exportData();},
      import:data=>{if(!offline||!sim)throw fault("NOT_APPLICABLE");sim.importData(data);}},
    on(event,fn){if(!listeners.has(event))listeners.set(event,new Set());listeners.get(event).add(fn);return ()=>listeners.get(event)?.delete(fn);},dispose};
  Object.defineProperty(window,"LanGame",{value:deepFreeze(sdk),writable:false});
})();


/**
 * HTML 材料全屏渲染壳页逻辑：
 * - 顶部浮动工具条（返回 / 收起展开）
 * - 教师端空闲加载绘图板（复用 teacher_whiteboard.js，覆盖在 iframe 之上）
 * - 学生端学习进度心跳（active 时长；HTML 包页面内部滚动无法统一度量，按时长判定）
 */

const shellConfig = window.MATERIAL_RENDER_SHELL || {};
const viewerContext = window.MATERIAL_VIEWER_CONTEXT || {};
const viewerAssets = window.MATERIAL_VIEWER_ASSETS || {};

function initTopbar() {
    const topbar = document.getElementById('render-shell-topbar');
    const collapseBtn = document.getElementById('render-shell-collapse');
    const expandBtn = document.getElementById('render-shell-expand');
    const backBtn = document.getElementById('render-shell-back');
    const homeBtn = document.getElementById('render-shell-home');
    const forwardBtn = document.getElementById('render-shell-forward');
    const frameEl = document.getElementById('render-shell-frame');

    // 返回/前进走会话历史：iframe 内的链接跳转会进入同一份历史栈，
    // 因此先逐步回退包内导航，退无可退时自然离开壳页回到平台上一页。
    backBtn?.addEventListener('click', () => {
        if (window.history.length > 1) {
            window.history.back();
        } else {
            window.location.href = '/dashboard';
        }
    });
    forwardBtn?.addEventListener('click', () => {
        window.history.forward();
    });
    homeBtn?.addEventListener('click', () => {
        const nodeId = Number(shellConfig.packageRootId || shellConfig.nodeId || 0);
        if (!frameEl || !nodeId) return;
        // 包默认入口（main.html）；已在首页时改为强制刷新。
        const homeSrc = `/materials/render/${nodeId}/`;
        try {
            const currentPath = String(frameEl.contentWindow?.location?.pathname || '');
            if (currentPath === homeSrc) {
                frameEl.contentWindow?.location?.reload();
                return;
            }
        } catch {
            /* 读取失败则直接重设 src。 */
        }
        frameEl.src = homeSrc;
    });
    collapseBtn?.addEventListener('click', () => {
        if (topbar) topbar.hidden = true;
        if (expandBtn) expandBtn.hidden = false;
    });
    expandBtn?.addEventListener('click', () => {
        if (topbar) topbar.hidden = false;
        if (expandBtn) expandBtn.hidden = true;
    });
}

function initPackageBadgeSync() {
    // HTML 包内部导航（首页↔课次）时，同步壳页徽章与文档标题（iframe 同源可读）。
    if (!shellConfig.isHtmlPackage) return;
    const badge = document.getElementById('render-shell-badge');
    const frameEl = document.getElementById('render-shell-frame');
    if (!badge || !frameEl) return;
    frameEl.addEventListener('load', () => {
        try {
            const framePath = String(frameEl.contentWindow?.location?.pathname || '');
            const match = framePath.match(/lesson[_-]?0*(\d{1,3})\.html?$/i);
            badge.textContent = match ? `第 ${Number(match[1])} 次课` : '课程首页';
            const frameTitle = frameEl.contentDocument?.title;
            if (frameTitle) document.title = frameTitle;
        } catch {
            /* 跨源或未就绪时保持原徽章。 */
        }
    });
}

async function initSlideRewriteEntry() {
    if(viewerContext.userRole!=='teacher'||!shellConfig.isHtmlPackage)return;
    const topbar=document.getElementById('render-shell-topbar'),frame=document.getElementById('render-shell-frame'),collapse=document.getElementById('render-shell-collapse');
    if(!topbar||!frame)return;
    const edit=document.createElement('a'),ai=document.createElement('button'),notice=document.createElement('span');
    edit.className=ai.className='btn btn-ghost btn-sm';edit.textContent='编辑学习文档';edit.id='render-shell-edit';ai.textContent='AI 改页';ai.type='button';ai.id='render-shell-slide-rewrite';notice.setAttribute('role','status');
    edit.hidden=ai.hidden=true;topbar.insertBefore(edit,collapse);topbar.insertBefore(ai,collapse);topbar.insertBefore(notice,collapse);
    let target=null,sequence=0;
    const editorUrl=(withAi=false)=>{
        if(!target?.editable)return '';
        const url=new URL(target.editor_url,location.origin);url.searchParams.set('return_to',location.pathname+location.search);
        const doc=frame.contentDocument,win=frame.contentWindow;
        if(!doc?.body.classList.contains('article-page')){
            const active=doc?.querySelector('.slide.active'),id=active?.dataset.ldSlideId;
            const page=String(win?.location.hash||'').match(/^#\/(\d+)/);
            if(id)url.searchParams.set('slide_id',id);else if(page)url.searchParams.set('slide',page[1]);
            else if(withAi)throw new Error('尚未定位当前页，请先切换到具体幻灯片。');
        }else if(withAi)throw new Error('文章模式下请先切换到幻灯片，再选择要改进的页面。');
        if(withAi)url.searchParams.set('ai','page');return url.pathname+url.search;
    };
    async function refresh(){
        const current=++sequence;target=null;edit.hidden=ai.hidden=true;notice.textContent='';
        try{
            const url=new URL(frame.contentWindow.location.href),match=url.pathname.match(/^\/materials\/render\/(\d+)\/(.*)$/);
            if(url.origin!==location.origin||!match)return;
            const response=await fetch('/api/lessondoc/editor/editability/'+match[1]+'?path='+encodeURIComponent(decodeURIComponent(match[2])),{credentials:'same-origin'});
            if(!response.ok||current!==sequence)return;target=(await response.json()).result;
            if(target.editable){edit.textContent='编辑学习文档';edit.href=editorUrl();edit.hidden=false;ai.hidden=!target.lesson_no;}
            else if(target.legacy_convertible){edit.textContent='转换并编辑';edit.href='#';edit.hidden=false;}
        }catch{/* A failed probe must not interrupt reading. */}
    }
    edit.addEventListener('click',async event=>{
        event.preventDefault();try{
            if(target?.editable){location.href=editorUrl();return;}
            if(target?.legacy_convertible){const {openLegacyConversion}=await import('./lessondoc_legacy_ui.js');openLegacyConversion(target.root_material_id);}
        }catch(e){notice.textContent=e.message;}
    });
    ai.addEventListener('click',()=>{try{location.href=editorUrl(true);}catch(e){notice.textContent=e.message;}});
    frame.addEventListener('load',refresh);await refresh();
}

function loadTeacherWhiteboardWhenIdle() {
    if (viewerContext.userRole !== 'teacher') return;
    const loadWhiteboard = () => {
        import(viewerAssets.teacherWhiteboard || './teacher_whiteboard.js').catch((error) => {
            console.warn('Teacher whiteboard failed to load:', error);
        });
    };
    if (typeof window.requestIdleCallback === 'function') {
        window.requestIdleCallback(loadWhiteboard, { timeout: 2000 });
    } else {
        window.setTimeout(loadWhiteboard, 800);
    }
}

function initLearningProgressHeartbeat() {
    const classOfferingId = Number(shellConfig.classOfferingId || 0);
    const materialId = Number(shellConfig.entryMaterialId || 0);
    if (viewerContext.userRole !== 'student' || !classOfferingId || !materialId) {
        return;
    }

    const endpoint = `/api/classrooms/${classOfferingId}/learning/material-progress`;
    const startedAt = performance.now();
    let lastTickAt = startedAt;
    let activeSeconds = 0;
    let pending = false;

    const syncActivity = () => {
        const now = performance.now();
        const delta = Math.max(0, Math.min(15, (now - lastTickAt) / 1000));
        if (document.visibilityState === 'visible') {
            activeSeconds += delta;
        }
        lastTickAt = now;
    };

    const buildPayload = () => ({
        material_id: materialId,
        session_id: shellConfig.sessionId ? Number(shellConfig.sessionId) : null,
        duration_seconds: Math.round(Math.max(0, (performance.now() - startedAt) / 1000)),
        active_seconds: Math.round(activeSeconds),
        scroll_ratio: 0,
        completed: activeSeconds >= 180,
        page_key: 'material_render_shell',
    });

    const sendProgress = async (options = {}) => {
        syncActivity();
        const payload = buildPayload();
        if (!options.force && payload.duration_seconds < 8) return;
        const body = JSON.stringify(payload);
        if (options.final && navigator.sendBeacon) {
            navigator.sendBeacon(endpoint, new Blob([body], { type: 'application/json' }));
            return;
        }
        if (pending) return;
        pending = true;
        try {
            await fetch(endpoint, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body,
            });
        } catch {
            /* 学习进度是尽力而为项，网络失败静默。 */
        } finally {
            pending = false;
        }
    };

    window.setInterval(syncActivity, 5000);
    window.setInterval(() => sendProgress(), 45000);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
            sendProgress({ force: true, final: true });
        }
    });
    window.addEventListener('pagehide', () => sendProgress({ force: true, final: true }));
}

initTopbar();
initPackageBadgeSync();
initSlideRewriteEntry();
loadTeacherWhiteboardWhenIdle();
initLearningProgressHeartbeat();

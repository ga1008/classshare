/**
 * HTML 材料全屏渲染壳页逻辑：
 * - 顶部浮动工具条（返回 / 收起展开）
 * - 教师端空闲加载绘图板（复用 teacher_whiteboard.js，覆盖在 iframe 之上）
 * - 学生端学习进度心跳（active 时长；HTML 包页面内部滚动无法统一度量，按时长判定）
 */

import { adoptDomainControl } from './lq/domain-controls.js';
import { startMaterialLearningTracker } from './material_learning_tracker.js';

const shellConfig = window.MATERIAL_RENDER_SHELL || {};
const viewerContext = window.MATERIAL_VIEWER_CONTEXT || {};
const viewerAssets = window.MATERIAL_VIEWER_ASSETS || {};
const renderFrame = document.getElementById('render-shell-frame');
let frameLocation = null;

function initFrameLocation() {
    if (!renderFrame) return;
    const bases = [
        [shellConfig.iframeBase, shellConfig.nodeId],
        [shellConfig.homeSrc, shellConfig.packageRootId],
    ];
    window.addEventListener('message', event => {
        if (event.source !== renderFrame.contentWindow || event.origin !== 'null') return;
        const data = event.data;
        if (data?.type !== 'lanshare:material-frame-location' || typeof data.path !== 'string') return;
        const match = bases.find(([base]) => base && data.path.startsWith(base));
        if (!match) return;
        const [base, nodeId] = match;
        try {
            const frameUrl = new URL(data.path, location.origin);
            if (frameUrl.origin !== location.origin || !frameUrl.pathname.startsWith(base)) return;
            frameUrl.hash = String(data.hash || '').slice(0, 128);
            frameLocation = {
                nodeId,
                subpath: decodeURIComponent(frameUrl.pathname.slice(base.length)),
                title: String(data.title || '').slice(0, 160),
                article: data.article === true,
                slideId: String(data.slideId || '').slice(0, 128),
                hash: frameUrl.hash,
            };
            renderFrame.dataset.materialFrameUrl = frameUrl.href;
        } catch { return; }
        renderFrame.dispatchEvent(new Event('lanshare:material-location'));
    });
    const ready = () => renderFrame.contentWindow?.postMessage({ type: 'lanshare:material-frame-ready' }, '*');
    renderFrame.addEventListener('load', ready);
    ready();
}

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
        if (frameEl && shellConfig.homeSrc) frameEl.src = shellConfig.homeSrc;
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
    if (!shellConfig.isHtmlPackage) return;
    const badge = document.getElementById('render-shell-badge');
    if (!badge || !renderFrame) return;
    renderFrame.addEventListener('lanshare:material-location', () => {
        const match = frameLocation.subpath.match(/lesson[_-]?0*(\d{1,3})\.html?$/i);
        badge.textContent = match ? `第 ${Number(match[1])} 次课` : '课程首页';
        if (frameLocation.title) document.title = frameLocation.title;
    });
}

async function initSlideRewriteEntry() {
    if(viewerContext.userRole!=='teacher'||!shellConfig.isHtmlPackage)return;
    const topbar=document.getElementById('render-shell-topbar'),frame=document.getElementById('render-shell-frame'),collapse=document.getElementById('render-shell-collapse');
    if(!topbar||!frame)return;
    const edit=document.createElement('a'),ai=document.createElement('button'),notice=document.createElement('span');
    edit.className=ai.className='btn btn-ghost btn-sm';edit.textContent='编辑学习文档';edit.id='render-shell-edit';ai.textContent='AI 改页';ai.type='button';ai.id='render-shell-slide-rewrite';notice.setAttribute('role','status');
    adoptDomainControl(edit,{kind:'button',variant:'ghost'});adoptDomainControl(ai,{variant:'ghost'});
    edit.hidden=ai.hidden=true;topbar.insertBefore(edit,collapse);topbar.insertBefore(ai,collapse);topbar.insertBefore(notice,collapse);
    let target=null,sequence=0,lastLocation='';
    const editorUrl=(withAi=false)=>{
        if(!target?.editable)return '';
        const url=new URL(target.editor_url,location.origin);url.searchParams.set('return_to',location.pathname+location.search);
        if(!frameLocation?.article){
            const id=frameLocation?.slideId;
            const page=String(frameLocation?.hash||'').match(/^#\/(\d+)/);
            if(id)url.searchParams.set('slide_id',id);else if(page)url.searchParams.set('slide',page[1]);
            else if(withAi)throw new Error('尚未定位当前页，请先切换到具体幻灯片。');
        }else if(withAi)throw new Error('文章模式下请先切换到幻灯片，再选择要改进的页面。');
        if(withAi)url.searchParams.set('ai','page');return url.pathname+url.search;
    };
    async function refresh(){
        if (!frameLocation) return;
        const {nodeId,subpath}=frameLocation;
        const locationKey=`${nodeId}/${subpath}`;
        if(locationKey===lastLocation)return;
        lastLocation=locationKey;
        const current=++sequence;target=null;edit.hidden=ai.hidden=true;notice.textContent='';
        try{
            const response=await fetch('/api/lessondoc/editor/editability/'+nodeId+'?path='+encodeURIComponent(subpath),{credentials:'same-origin'});
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
    frame.addEventListener('lanshare:material-location',refresh);await refresh();
}

/**
 * 白板打开时让文档侧降级。
 *
 * 白板是覆盖在这张 iframe 之上的半透明层：白板每重绘一帧，浏览器都要把 iframe 里
 * 整棵文档树重新合成一次。通过消息暂停课件动画，完全遮挡时将 iframe 从合成里摘掉。
 */
function initWhiteboardDocQuiet() {
    if (viewerContext.userRole !== 'teacher') return;
    const frameEl = document.getElementById('render-shell-frame');
    if (!frameEl) return;
    let state = { open: false, backgroundOpacity: 1 };

    const apply = () => {
        frameEl.contentWindow?.postMessage({ type: 'lanshare:material-frame-quiet', open: state.open }, '*');
        // 背景不透明时文档完全被盖住，隐藏 iframe 可以整棵树退出合成。
        frameEl.style.visibility = state.open && state.backgroundOpacity >= 0.98 ? 'hidden' : '';
    };

    window.addEventListener('teacher-whiteboard:state', (event) => {
        state = { ...state, ...(event.detail || {}) };
        apply();
    });
    frameEl.addEventListener('load', apply);
    apply();
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
    return startMaterialLearningTracker({ context: { ...viewerContext,
        classOfferingId: Number(shellConfig.classOfferingId || viewerContext.classOfferingId || 0),
        materialId: Number(shellConfig.entryMaterialId || viewerContext.materialId || 0),
        sessionId: shellConfig.sessionId || viewerContext.sessionId,
    }, pageKey: 'material_render_shell' });
}

initTopbar();
initPackageBadgeSync();
initSlideRewriteEntry();
initWhiteboardDocQuiet();
initFrameLocation();
loadTeacherWhiteboardWhenIdle();
initLearningProgressHeartbeat();

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
        const nodeId = Number(shellConfig.nodeId || 0);
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
    // LessonDoc 单页重写（R2）：仅教师 + LessonDoc 包时在工具条注入「改这一页」。
    // 壳页只知道 nodeId，先按包根反查 pack；旧手写包（无登记行）不显示入口。
    if (viewerContext.userRole !== 'teacher' || !shellConfig.isHtmlPackage) return;
    const nodeId = Number(shellConfig.nodeId || 0);
    const topbar = document.getElementById('render-shell-topbar');
    const collapseBtn = document.getElementById('render-shell-collapse');
    const frameEl = document.getElementById('render-shell-frame');
    if (!nodeId || !topbar || !frameEl) return;

    let packId = 0;
    try {
        const resp = await fetch(`/api/lessondoc/packs/by-root/${nodeId}`, {
            credentials: 'same-origin', headers: { Accept: 'application/json' },
        });
        if (!resp.ok) return;
        packId = Number((await resp.json())?.pack?.id || 0);
    } catch {
        return; // 探测失败静默——入口缺席不影响阅读
    }
    if (!packId) return;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-ghost btn-sm';
    btn.id = 'render-shell-slide-rewrite';
    btn.textContent = '✏ 改这一页';
    btn.title = '让 AI 只重写当前显示的这一页（其余页面不动）';
    topbar.insertBefore(btn, collapseBtn || null);

    const currentLocation = () => {
        try {
            const win = frameEl.contentWindow;
            const path = String(win?.location?.pathname || '');
            const lessonMatch = path.match(/lesson[_-]?0*(\d{1,3})\.html?$/i);
            const pageMatch = String(win?.location?.hash || '').match(/^#\/(\d+)/);
            return {
                lessonNo: lessonMatch ? Number(lessonMatch[1]) : 0,
                slideNo: pageMatch ? Number(pageMatch[1]) : 1,
            };
        } catch {
            return { lessonNo: 0, slideNo: 1 };
        }
    };

    btn.addEventListener('click', async () => {
        const { lessonNo, slideNo } = currentLocation();
        if (!lessonNo) {
            window.alert('请先进入某个课次页面，再重写当前页（课程首页不支持单页重写）。');
            return;
        }
        const hint = window.prompt(
            `重写第 ${lessonNo} 课·第 ${slideNo} 页：\n请输入改进要求（留空 = 优化表达与版式）`, '');
        if (hint === null) return;
        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'AI 编写中…';
        try {
            const resp = await fetch(
                `/api/lessondoc/packs/${packId}/lessons/${lessonNo}/slides/${slideNo}/rewrite`,
                {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_hint: hint }),
                },
            );
            const data = await resp.json().catch(() => null);
            if (!resp.ok) {
                const message = data?.detail?.message || data?.detail || '重写失败，请重试';
                throw new Error(typeof message === 'string' ? message : '重写失败，请重试');
            }
            // 刷新 iframe 并保持当前页码，改动立即可见
            try {
                frameEl.contentWindow?.location?.reload();
            } catch {
                frameEl.src = String(frameEl.getAttribute('src') || '');
            }
            const warnCount = (data?.warnings || []).length;
            btn.textContent = warnCount ? `✓ 已重写(${warnCount}处降级)` : '✓ 已重写';
            window.setTimeout(() => { btn.textContent = originalText; }, 3000);
        } catch (error) {
            window.alert(error?.message || '重写失败，请重试');
            btn.textContent = originalText;
        } finally {
            btn.disabled = false;
        }
    });
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

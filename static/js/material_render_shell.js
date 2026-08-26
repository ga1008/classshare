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

    backBtn?.addEventListener('click', () => {
        if (window.history.length > 1) {
            window.history.back();
        } else {
            window.location.href = '/dashboard';
        }
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
loadTeacherWhiteboardWhenIdle();
initLearningProgressHeartbeat();

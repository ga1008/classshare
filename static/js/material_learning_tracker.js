/** Material reading reuses the classroom collector's bounded durable transport. */
export function startMaterialLearningTracker({ context, pageKey, frameElement = null, getProgress = () => ({}), onReceipt = null }) {
    const offeringId = Number(context?.classOfferingId || 0);
    const materialId = Number(context?.materialId || 0);
    const userId = Number(context?.userId || 0);
    if (context?.userRole !== 'student' || !offeringId || !materialId || !userId) return null;
    const Tracker = window.BehaviorTracker;
    if (typeof Tracker !== 'function') {
        window.dispatchEvent(new CustomEvent('behavior:unavailable', { detail: { error_code: 'material_collector_unavailable' } }));
        return null;
    }
    class MaterialTracker extends Tracker {
        constructor(options) {
            super(options);
            this.materialFrameActivity = event => {
                if (!this.started || this.isCollectionSuspended() || event.source !== frameElement?.contentWindow
                    || event.origin !== 'null' || event.data?.type !== 'lanshare:material-frame-activity') return;
                const state = this.readPresenceState();
                this.changePresence(state);
                if (state.visibility_state === 'visible' && state.focused) this.handleActivity();
            };
        }
        start() {
            const wasStarted = this.started;
            super.start();
            if (!wasStarted && this.started && frameElement) window.addEventListener('message', this.materialFrameActivity);
            return this;
        }
        stop(options) {
            window.removeEventListener('message', this.materialFrameActivity);
            super.stop(options);
        }
        handleBlur() {
            if (!frameElement) return super.handleBlur();
            // Focus inside the sandbox belongs to this document. A parent
            // window blur alone must not turn an active lesson into idle time.
            const state = this.readPresenceState();
            this.changePresence(state);
            this.log('page_focus', '材料页面焦点变化', { focused: state.focused });
            this.flushSoon();
        }
        makeBatchBody(events) {
            const body = super.makeBatchBody(events);
            let progress = {};
            try { progress = getProgress() || {}; }
            catch { this.recordDiagnostic('client_material_progress_unavailable'); }
            const scroll = Number(progress.scroll_ratio || 0);
            return { ...body, progress_version: 2, material_id: materialId,
                session_id: context.sessionId ? Number(context.sessionId) : null,
                scroll_ratio: Number.isFinite(scroll) ? Math.max(0, Math.min(1, scroll)) : 0,
                completed: progress.completed === true };
        }
    }
    return new MaterialTracker({ classOfferingId: offeringId, pageKey,
        userKey: `student:${userId}`, storageNamespace: `material_${materialId}`,
        endpoint: `/api/classrooms/${offeringId}/learning/material-progress`,
        onReceipt,
    }).start();
}

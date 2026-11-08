/** Material reading reuses the classroom collector's bounded durable transport. */
export function startMaterialLearningTracker({ context, pageKey, getProgress = () => ({}), onReceipt = null }) {
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

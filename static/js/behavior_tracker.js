(function () {
    const MAX_PRESENCE_INTERVAL_MS = 90000;
    const PRESENCE_IDLE_MS = 150000;
    const TERMINAL_BATCH_ERRORS = new Set([
        'batch_payload_conflict', 'batch_expired', 'batch_timestamp_invalid',
        'batch_payload_invalid', 'batch_payload_too_large', 'classroom_unavailable', 'actor_unavailable',
    ]);

    function presenceNow() {
        const clock = window.performance;
        return Number.isFinite(clock?.timeOrigin) ? Math.floor(clock.timeOrigin + clock.now()) : Date.now();
    }

    class BehaviorTracker {
        constructor(options = {}) {
            this.classOfferingId = Number(options.classOfferingId || 0);
            this.pageKey = String(options.pageKey || 'page').trim() || 'page';
            this.endpoint = options.endpoint || `/api/classrooms/${this.classOfferingId}/behavior/batch`;
            this.heartbeatMs = Number(options.heartbeatMs || 60000);
            this.maxBatchSize = Number(options.maxBatchSize || 16);
            this.userKey = String(options.userKey || '').trim();
            this.storageKey = this.userKey ? `lanshare:behavior:v1:${this.userKey}:${this.classOfferingId}` : null;
            this.diagnosticsEndpoint = options.diagnosticsEndpoint || `/api/classrooms/${this.classOfferingId}/behavior/diagnostics`;
            this.events = [];
            this.pendingBatch = null;
            this.diagnostics = [];
            this.quarantinedBatches = [];
            this.quarantinedDiagnostics = [];
            this.retryAttempt = 0;
            this.retryAt = 0;
            this.diagnosticRetryAttempt = 0;
            this.diagnosticRetryAt = 0;
            this.retryAfterAt = 0;
            this.lastAttemptAt = 0;
            this.transportState = 'healthy';
            this.suspendedAt = null;
            this.storageUnavailable = false;
            this.paused = false;
            this.requestTimeoutMs = Number(options.requestTimeoutMs || 12000);
            this.maxQueuedEvents = Math.max(48, Number(options.maxQueuedEvents || 512));
            this.restore({ freshDocument: true });
            this.flushTimer = null;
            this.flushDueAt = 0;
            this.heartbeatTimer = null;
            this.flushing = false;
            this.started = false;
            this.aiPanelOpen = false;
            this.lastInteractionAt = 0;
            this.presenceAt = 0;
            this.presenceState = null;
            this.handleActivity = this.handleActivity.bind(this);
            this.handleVisibilityChange = this.handleVisibilityChange.bind(this);
            this.handleFocus = this.handleFocus.bind(this);
            this.handleBlur = this.handleBlur.bind(this);
            this.handlePageHide = this.handlePageHide.bind(this);
            this.handlePageShow = this.handlePageShow.bind(this);
            this.handleOnline = this.handleOnline.bind(this);
            this.handleSessionChange = this.handleSessionChange.bind(this);
        }

        restore({ freshDocument = false } = {}) {
            if (!this.storageKey) return;
            if (this.paused && !freshDocument) return;
            try {
                const raw = window.sessionStorage.getItem(this.storageKey);
                if (!raw) {
                    this.events = [];
                    this.pendingBatch = null;
                    this.diagnostics = [];
                    this.transportState = 'healthy';
                    this.suspendedAt = null;
                    return;
                }
                const state = JSON.parse(raw);
                if (state?.userKey !== this.userKey) return;
                this.events = Array.isArray(state.events) ? state.events : [];
                this.pendingBatch = state.pendingBatch?.body?.user_key === this.userKey ? state.pendingBatch : null;
                this.diagnostics = Array.isArray(state.diagnostics) ? state.diagnostics : [];
                this.quarantinedBatches = Array.isArray(state.quarantinedBatches) ? state.quarantinedBatches : [];
                this.quarantinedDiagnostics = Array.isArray(state.quarantinedDiagnostics) ? state.quarantinedDiagnostics : [];
                this.transportState = ['auth_required', 'identity_changed', 'blocked', 'retrying'].includes(state.transportState)
                    ? state.transportState : 'healthy';
                this.suspendedAt = Number.isFinite(state.suspendedAt) ? state.suspendedAt : null;
                // A freshly rendered page supplies the authenticated identity. A bfcache page does not.
                if (freshDocument && this.transportState === 'identity_changed') {
                    this.transportState = 'healthy';
                    this.suspendedAt = null;
                }
                this.paused = this.transportState === 'identity_changed';
            } catch (error) {
                console.warn('behavior recovery unavailable', error);
                this.recordDiagnostic('client_recovery_unavailable');
            }
        }

        persist() {
            if (!this.storageKey) return;
            try {
                if (!this.events.length && !this.pendingBatch && !this.diagnostics.length
                    && !this.quarantinedBatches.length && !this.quarantinedDiagnostics.length) {
                    window.sessionStorage.removeItem(this.storageKey);
                } else {
                    window.sessionStorage.setItem(this.storageKey, JSON.stringify({
                        userKey: this.userKey, events: this.events, pendingBatch: this.pendingBatch,
                        diagnostics: this.diagnostics, quarantinedBatches: this.quarantinedBatches,
                        quarantinedDiagnostics: this.quarantinedDiagnostics,
                        transportState: this.transportState, suspendedAt: this.suspendedAt,
                    }));
                }
                this.storageUnavailable = false;
            } catch (error) {
                console.warn('behavior local buffer unavailable', error);
                if (!this.storageUnavailable) this.recordDiagnostic('client_storage_unavailable');
                this.storageUnavailable = true;
            }
        }

        isCollectionSuspended() {
            return ['auth_required', 'identity_changed', 'blocked'].includes(this.transportState);
        }

        setTransportState(state, errorCode = null) {
            const wasSuspended = this.isCollectionSuspended();
            this.transportState = state;
            this.paused = state === 'identity_changed';
            const now = presenceNow();
            if (this.isCollectionSuspended() && this.suspendedAt === null) this.suspendedAt = now;
            if (wasSuspended && !this.isCollectionSuspended()) {
                this.recordDiagnostic('collection_resumed', { interval_start_ms: this.suspendedAt, interval_end_ms: now });
                this.suspendedAt = null;
                this.presenceAt = now;
                this.lastInteractionAt = now;
                this.presenceState = this.readPresenceState();
                if (this.started) this.enqueueEvent(this.buildHeartbeatEvent(now));
            }
            window.dispatchEvent(new CustomEvent('behavior:status', { detail: {
                state, error_code: errorCode, queued: this.events.length,
                diagnostic_count: this.diagnostics.length, quarantined_count: this.quarantinedBatches.length,
            } }));
        }

        recordDiagnostic(errorCode, details = {}) {
            // Retries describe one incident, rather than creating a diagnostic for every attempt.
            const affectedBatch = details.affected_batch_id || null;
            const existing = this.diagnostics.find(item => item.error_code === errorCode
                && (item.affected_batch_id || null) === affectedBatch
                && item.interval_start_ms === details.interval_start_ms && item.interval_end_ms === details.interval_end_ms);
            if (existing) return existing;
            const nonce = window.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
            const item = { diagnostic_id: `d1_${Date.now()}_${nonce}`, error_code: errorCode,
                observed_ms: presenceNow(), ...details };
            this.diagnostics.push(item);
            return item;
        }

        batchDiagnostic(errorCode, body) {
            const intervals = (body?.events || []).map(item => item.payload)
                .filter(payload => Number.isFinite(payload?.interval_start_ms) && Number.isFinite(payload?.interval_end_ms)
                    && payload.interval_start_ms <= payload.interval_end_ms);
            const batchId = String(body?.batch_id || '');
            return this.recordDiagnostic(errorCode, {
                ...(/^b1_[0-9]{10,16}_[A-Za-z0-9_-]+$/.test(batchId) && batchId.length <= 96 ? { affected_batch_id: batchId } : {}),
                event_count: Math.min(512, body?.events?.length || 0),
                ...(intervals.length ? {
                    interval_start_ms: Math.min(...intervals.map(item => item.interval_start_ms)),
                    interval_end_ms: Math.max(...intervals.map(item => item.interval_end_ms)),
                } : {}),
            });
        }

        quarantineBatch(body, errorCode) {
            if (!body) return;
            if (!this.quarantinedBatches.some(item => item.body.batch_id === body.batch_id)) {
                this.quarantinedBatches.push({ body, error_code: errorCode, observed_ms: presenceNow() });
            }
            this.batchDiagnostic(errorCode, body);
            if (this.pendingBatch?.body.batch_id === body.batch_id) this.pendingBatch = null;
        }

        isolateIdentity() {
            this.recordDiagnostic('identity_changed');
            this.quarantineBatch(this.pendingBatch?.body, 'identity_changed');
            if (this.events.length) this.quarantineBatch(this.makeBatchBody(this.events.splice(0)), 'identity_changed');
            this.setTransportState('identity_changed', 'identity_changed');
            this.persist();
        }

        enqueueEvent(event) {
            if (!event || !this.classOfferingId || this.isCollectionSuspended()) return false;
            // Preserve heartbeat/focus ordering and activity counts. Never trim an unacknowledged batch.
            if (this.events.length >= this.maxQueuedEvents) {
                window.dispatchEvent(new CustomEvent('behavior:backpressure', { detail: { queued: this.events.length } }));
                this.recordDiagnostic('client_buffer_full', { event_count: this.events.length });
                this.persist();
                this.flushSoon();
                return false;
            }
            this.events.push(event);
            this.persist();
            return true;
        }

        handleOnline() {
            if (this.paused) return;
            this.retryAt = Math.max(this.retryAfterAt, this.lastAttemptAt + 3000);
            this.flushSoon(250 + Math.random() * 750);
        }

        handleSessionChange(event) {
            const userKey = event?.detail?.user_key;
            if (userKey && userKey !== this.userKey) this.isolateIdentity();
            else this.handleOnline();
        }

        handlePageShow(event) {
            if (event.persisted) {
                this.restore();
                this.start();
            }
        }

        start() {
            if (this.started || !this.classOfferingId) {
                return this;
            }
            this.started = true;
            this.presenceAt = this.lastInteractionAt = presenceNow();
            this.presenceState = this.readPresenceState();
            document.addEventListener('pointerdown', this.handleActivity, true);
            document.addEventListener('keydown', this.handleActivity, true);
            document.addEventListener('scroll', this.handleActivity, true);
            document.addEventListener('visibilitychange', this.handleVisibilityChange);
            window.addEventListener('focus', this.handleFocus);
            window.addEventListener('blur', this.handleBlur);
            window.addEventListener('pagehide', this.handlePageHide);
            window.addEventListener('pageshow', this.handlePageShow);
            window.addEventListener('online', this.handleOnline);
            window.addEventListener('behavior:session-change', this.handleSessionChange);
            this.heartbeatTimer = window.setInterval(() => this.flushHeartbeat(), this.heartbeatMs);
            this.log('page_enter', `进入页面：${this.pageKey}`, { page_key: this.pageKey });
            if (!this.isCollectionSuspended()) this.enqueueEvent(this.buildHeartbeatEvent(this.presenceAt));
            this.flushSoon();
            return this;
        }

        stop({ resumeOnPageShow = false } = {}) {
            if (!this.started) {
                return;
            }
            this.started = false;
            document.removeEventListener('pointerdown', this.handleActivity, true);
            document.removeEventListener('keydown', this.handleActivity, true);
            document.removeEventListener('scroll', this.handleActivity, true);
            document.removeEventListener('visibilitychange', this.handleVisibilityChange);
            window.removeEventListener('focus', this.handleFocus);
            window.removeEventListener('blur', this.handleBlur);
            window.removeEventListener('pagehide', this.handlePageHide);
            if (!resumeOnPageShow) window.removeEventListener('pageshow', this.handlePageShow);
            window.removeEventListener('online', this.handleOnline);
            window.removeEventListener('behavior:session-change', this.handleSessionChange);
            if (this.flushTimer) {
                window.clearTimeout(this.flushTimer);
                this.flushTimer = null;
            }
            if (this.heartbeatTimer) {
                window.clearInterval(this.heartbeatTimer);
                this.heartbeatTimer = null;
            }
            this.flushHeartbeat(true);
            this.presenceState = null;
        }

        handleActivity(event) {
            if (event?.isTrusted === false) return;
            const now = presenceNow();
            // Close the idle portion before this interaction starts a new active interval.
            if (now - this.lastInteractionAt >= PRESENCE_IDLE_MS) {
                this.samplePresence(now);
                this.flushSoon();
            }
            this.lastInteractionAt = now;
        }

        handleVisibilityChange() {
            const hidden = document.visibilityState === 'hidden';
            this.changePresence(this.readPresenceState());
            this.log(
                'page_visibility',
                hidden ? '页面切到后台' : '页面回到前台',
                { visibility_state: hidden ? 'hidden' : 'visible' }
            );
            this.flushSoon();
        }

        handleFocus() {
            this.changePresence({ ...this.readPresenceState(), focused: true });
            this.log('page_focus', '页面获得焦点', { focused: true });
            this.flushSoon();
            if (this.transportState !== 'healthy') this.handleOnline();
        }

        handleBlur() {
            this.changePresence({ ...this.readPresenceState(), focused: false });
            this.log('page_focus', '页面失去焦点', { focused: false });
            this.flushSoon();
        }

        handlePageHide(event) {
            this.stop({ resumeOnPageShow: Boolean(event?.persisted) });
        }

        log(actionType, summaryText, payload = {}, pageKey = this.pageKey) {
            if (!this.classOfferingId) {
                return;
            }
            this.enqueueEvent({
                action_type: String(actionType || 'page_action'),
                summary_text: String(summaryText || ''),
                page_key: pageKey,
                payload,
            });
            if (this.events.length >= this.maxBatchSize) {
                this.flushSoon(100);
            }
        }

        logClick(summaryText, payload = {}, pageKey = this.pageKey) {
            this.log('page_click', summaryText, payload, pageKey);
        }

        markAiChatOpen(open) {
            const normalized = Boolean(open);
            if (this.aiPanelOpen === normalized) {
                return;
            }
            this.aiPanelOpen = normalized;
            this.changePresence(this.readPresenceState());
            this.log(
                normalized ? 'ai_panel_open' : 'ai_panel_close',
                normalized ? '打开 AI 助手面板' : '关闭 AI 助手面板',
                { open: normalized },
                'ai_chat'
            );
            this.flushSoon(300);
        }

        readPresenceState() {
            return {
                visibility_state: document.visibilityState === 'visible' ? 'visible' : 'hidden',
                focused: document.hasFocus(),
                ai_panel_open: this.aiPanelOpen,
            };
        }

        buildHeartbeatEvent(now, start = null) {
            return {
                action_type: 'presence_heartbeat',
                summary_text: '',
                page_key: this.pageKey,
                payload: {
                    ...this.presenceState,
                    presence_version: 2,
                    presence_observed_ms: now,
                    idle_seconds: Math.max(0, Math.ceil((now - this.lastInteractionAt) / 1000)),
                    ...(start === null ? {} : { interval_start_ms: start, interval_end_ms: now }),
                },
            };
        }

        samplePresence(now) {
            const start = this.presenceAt;
            this.presenceAt = now;
            if (this.isCollectionSuspended()) return null;
            const validInterval = now > start && now - start <= MAX_PRESENCE_INTERVAL_MS;
            if (now - start > MAX_PRESENCE_INTERVAL_MS) {
                this.recordDiagnostic('client_observation_gap', { interval_start_ms: start, interval_end_ms: now });
            }
            const event = this.buildHeartbeatEvent(now, validInterval ? start : null);
            this.enqueueEvent(event);
            return event;
        }

        changePresence(nextState) {
            if (!this.presenceState) return;
            if (this.presenceState.visibility_state === nextState.visibility_state
                && this.presenceState.focused === nextState.focused
                && this.presenceState.ai_panel_open === nextState.ai_panel_open) return;
            const now = presenceNow();
            if (now > this.presenceAt) this.samplePresence(now);
            this.presenceState = nextState;
            this.presenceAt = now;
            this.enqueueEvent(this.buildHeartbeatEvent(now));
        }

        flushHeartbeat(sync = false) {
            if (!this.classOfferingId || !this.presenceState) {
                return;
            }
            const event = this.samplePresence(presenceNow());
            this.flush({ sync });
            if (sync && !this.isCollectionSuspended()) {
                // A visibility change can close the last active interval just before pagehide.
                // Send its evidence even when an older immutable batch blocks the buffer.
                const tail = this.events.filter(item => item.action_type === 'presence_heartbeat'
                    && item.payload?.interval_start_ms !== undefined).slice(-19);
                if (event?.payload.interval_start_ms !== undefined && !tail.includes(event)
                    && !this.pendingBatch?.body.events.includes(event)) tail.push(event);
                if (tail.length) this.sendUnconfirmed(this.makeBatchBody(tail));
            }
        }

        flushSoon(delay = 2200) {
            if (this.paused || !this.started) return;
            const retryAt = !this.pendingBatch && !this.events.length ? Math.max(this.retryAt, this.diagnosticRetryAt) : this.retryAt;
            const wait = Math.max(delay, retryAt - Date.now(), 0);
            const dueAt = Date.now() + wait;
            if (this.flushTimer && this.flushDueAt <= dueAt) return;
            if (this.flushTimer) window.clearTimeout(this.flushTimer);
            this.flushDueAt = dueAt;
            this.flushTimer = window.setTimeout(() => {
                this.flushTimer = null;
                this.flushDueAt = 0;
                this.flush();
            }, wait);
        }

        makeBatchBody(events) {
            const nonce = window.crypto?.randomUUID?.() || `${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
            return { batch_id: `b1_${Date.now()}_${nonce}`, user_key: this.userKey || null, page_key: this.pageKey, events };
        }

        prepareBatch() {
            if (this.pendingBatch) return this.pendingBatch;
            if (!this.events.length) return null;
            this.pendingBatch = { body: this.makeBatchBody(this.events.splice(0, Math.min(20, this.maxBatchSize))) };
            this.persist();
            return this.pendingBatch;
        }

        sendUnconfirmed(body) {
            if (this.isCollectionSuspended()) return;
            const data = JSON.stringify(body);
            try {
                if (navigator.sendBeacon?.(this.endpoint, new Blob([data], { type: 'application/json' }))) return;
            } catch {
                // Some browsers throw when the keepalive quota is exhausted.
            }
            fetch(this.endpoint, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin', body: data, keepalive: true,
            }).catch(() => {});
        }

        async flush({ sync = false } = {}) {
            if (!this.classOfferingId || this.paused) return;
            const pending = this.prepareBatch();
            if (sync) {
                // A beacon is an unacknowledged copy, including when fetch is already in flight.
                // Retain the exact batch in sessionStorage until a response confirms durable intake.
                if (pending) {
                    this.persist();
                    this.sendUnconfirmed(pending.body);
                }
                return;
            }
            const diagnosticItems = !pending ? this.diagnostics.slice(0, 20) : [];
            if (!pending && !diagnosticItems.length) return;
            if (this.flushing) return;
            if (Date.now() < Math.max(this.retryAt, pending ? 0 : this.diagnosticRetryAt)) {
                this.flushSoon();
                return;
            }
            this.flushing = true;
            this.lastAttemptAt = Date.now();
            const controller = new AbortController();
            let timedOut = false;
            const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, this.requestTimeoutMs);
            let retryAfterMs = 0;
            try {
                const response = await fetch(pending ? this.endpoint : this.diagnosticsEndpoint, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin', body: JSON.stringify(pending ? pending.body : {
                        user_key: this.userKey, diagnostics: diagnosticItems,
                    }), signal: controller.signal,
                });
                const retryHeader = response.headers.get('Retry-After');
                if (retryHeader) {
                    const seconds = Number(retryHeader);
                    const parsed = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(retryHeader) - Date.now();
                    retryAfterMs = Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
                    if (pending) this.retryAfterAt = Date.now() + retryAfterMs;
                }
                const receipt = typeof response.json === 'function' ? await response.json().catch(() => null) : null;
                if (this.paused) return;
                const failure = receipt?.collection_error || (typeof receipt?.detail === 'object' ? receipt.detail : null);
                if (failure?.error_code === 'identity_changed'
                    || (receipt?.current_user_key && receipt.current_user_key !== this.userKey)) {
                    this.isolateIdentity();
                    return;
                }
                if (!response.ok) {
                    const code = String(failure?.error_code || (response.status === 401 ? 'auth_required' : `http_${response.status}`));
                    if (response.status === 401 || code === 'auth_required') this.setTransportState('auth_required', code);
                    else if (response.status === 403) this.setTransportState('blocked', code);
                    if (pending && failure?.batch_disposition === 'quarantine'
                        && (TERMINAL_BATCH_ERRORS.has(code) || ['quarantined', 'discarded'].includes(receipt?.write_mode))) {
                        this.quarantineBatch(pending.body, code);
                        this.retryAttempt = 0;
                        this.retryAt = 0;
                        if (['classroom_unavailable', 'actor_unavailable'].includes(code)) this.setTransportState('blocked', code);
                        else this.setTransportState('healthy', code);
                        this.persist();
                        return;
                    }
                    if (!pending && failure?.batch_disposition === 'quarantine'
                        && ['diagnostic_timestamp_invalid', 'diagnostic_interval_invalid', 'diagnostic_payload_invalid'].includes(code)) {
                        const ids = new Set(diagnosticItems.map(item => item.diagnostic_id));
                        this.quarantinedDiagnostics.push({ diagnostics: diagnosticItems, error_code: code, observed_ms: presenceNow() });
                        this.diagnostics = this.diagnostics.filter(item => !ids.has(item.diagnostic_id));
                        this.recordDiagnostic(code);
                        this.diagnosticRetryAt = 0;
                        this.diagnosticRetryAttempt = 0;
                        this.persist();
                        return;
                    }
                    const failureError = new Error(`HTTP ${response.status}`);
                    failureError.errorCode = code;
                    throw failureError;
                }
                if (!receipt || receipt.status !== 'success' || receipt.degraded) {
                    throw new Error('Behavior intake was not confirmed');
                }
                if (pending) {
                    if (!['durable_queued', 'synced', 'noop'].includes(receipt.write_mode)) throw new Error('Behavior intake was not confirmed');
                    if (receipt.batch_id && receipt.batch_id !== pending.body.batch_id) throw new Error('Behavior receipt mismatch');
                    // A resumed bfcache page may have restored a newer shared-tab batch.
                    if (this.pendingBatch?.body.batch_id !== pending.body.batch_id) return;
                    this.pendingBatch = null;
                } else {
                    const accepted = new Set(receipt.accepted_diagnostic_ids || []);
                    if (!diagnosticItems.every(item => accepted.has(item.diagnostic_id))) throw new Error('Behavior diagnostic receipt mismatch');
                    this.diagnostics = this.diagnostics.filter(item => !accepted.has(item.diagnostic_id));
                }
                this.retryAttempt = 0;
                this.retryAt = 0;
                this.retryAfterAt = 0;
                if (!pending) {
                    this.diagnosticRetryAt = 0;
                    this.diagnosticRetryAttempt = 0;
                }
                this.setTransportState('healthy');
                this.persist();
            } catch (error) {
                if (this.paused) return;
                if (pending) this.retryAttempt += 1;
                else this.diagnosticRetryAttempt += 1;
                const attempt = pending ? this.retryAttempt : this.diagnosticRetryAttempt;
                const backoff = Math.min(60000, 1000 * (2 ** Math.min(attempt, 6)));
                const minimumWait = this.isCollectionSuspended() ? 15000 : 0;
                const nextAttemptAt = Date.now() + Math.max(retryAfterMs, backoff, minimumWait) + Math.random() * 1000;
                if (pending || this.isCollectionSuspended()) this.retryAt = nextAttemptAt;
                else this.diagnosticRetryAt = nextAttemptAt;
                const code = error.errorCode || (timedOut ? 'client_request_timeout' : 'client_request_unconfirmed');
                if (!this.isCollectionSuspended()) this.setTransportState('retrying', code);
                if (pending) this.batchDiagnostic(code, pending.body);
                else this.recordDiagnostic(code);
                this.persist();
                console.warn('behavior flush deferred', error);
            } finally {
                window.clearTimeout(timeout);
                this.flushing = false;
                if (this.pendingBatch || this.events.length || this.diagnostics.length) this.flushSoon(500);
            }
        }
    }

    window.BehaviorTracker = BehaviorTracker;
})();

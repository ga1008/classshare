(function () {
    class BehaviorTracker {
        constructor(options = {}) {
            this.classOfferingId = Number(options.classOfferingId || 0);
            this.pageKey = String(options.pageKey || 'page').trim() || 'page';
            this.endpoint = options.endpoint || `/api/classrooms/${this.classOfferingId}/behavior/batch`;
            this.heartbeatMs = Number(options.heartbeatMs || 60000);
            this.maxBatchSize = Number(options.maxBatchSize || 16);
            this.userKey = String(options.userKey || '').trim();
            this.storageKey = this.userKey ? `lanshare:behavior:v1:${this.userKey}:${this.classOfferingId}` : null;
            this.events = [];
            this.pendingBatch = null;
            this.retryAttempt = 0;
            this.retryAt = 0;
            this.paused = false;
            this.requestTimeoutMs = Number(options.requestTimeoutMs || 12000);
            this.maxQueuedEvents = Math.max(48, Number(options.maxQueuedEvents || 512));
            this.restore();
            this.flushTimer = null;
            this.heartbeatTimer = null;
            this.flushing = false;
            this.started = false;
            this.aiPanelOpen = false;
            this.lastInteractionAt = Date.now();
            this.handleActivity = this.handleActivity.bind(this);
            this.handleVisibilityChange = this.handleVisibilityChange.bind(this);
            this.handleFocus = this.handleFocus.bind(this);
            this.handleBlur = this.handleBlur.bind(this);
            this.handlePageHide = this.handlePageHide.bind(this);
            this.handlePageShow = this.handlePageShow.bind(this);
            this.handleOnline = this.handleOnline.bind(this);
        }

        restore() {
            if (!this.storageKey) return;
            try {
                const raw = window.sessionStorage.getItem(this.storageKey);
                if (!raw) {
                    this.events = [];
                    this.pendingBatch = null;
                    return;
                }
                const state = JSON.parse(raw);
                if (state?.userKey !== this.userKey) return;
                this.events = Array.isArray(state.events) ? state.events : [];
                this.pendingBatch = state.pendingBatch?.body?.user_key === this.userKey ? state.pendingBatch : null;
            } catch (error) {
                console.warn('behavior recovery unavailable', error);
            }
        }

        persist() {
            if (!this.storageKey) return;
            try {
                if (!this.events.length && !this.pendingBatch) {
                    window.sessionStorage.removeItem(this.storageKey);
                } else {
                    window.sessionStorage.setItem(this.storageKey, JSON.stringify({
                        userKey: this.userKey, events: this.events, pendingBatch: this.pendingBatch,
                    }));
                }
            } catch (error) {
                console.warn('behavior local buffer unavailable', error);
            }
        }

        enqueueEvent(event) {
            if (!event || !this.classOfferingId) return false;
            // Preserve heartbeat/focus ordering and activity counts. Never trim an unacknowledged batch.
            if (this.events.length >= this.maxQueuedEvents) {
                window.dispatchEvent(new CustomEvent('behavior:backpressure', { detail: { queued: this.events.length } }));
                this.flushSoon();
                return false;
            }
            this.events.push(event);
            this.persist();
            return true;
        }

        handleOnline() {
            this.retryAt = 0;
            this.flushSoon(250 + Math.random() * 750);
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
            document.addEventListener('pointerdown', this.handleActivity, true);
            document.addEventListener('keydown', this.handleActivity, true);
            document.addEventListener('scroll', this.handleActivity, true);
            document.addEventListener('visibilitychange', this.handleVisibilityChange);
            window.addEventListener('focus', this.handleFocus);
            window.addEventListener('blur', this.handleBlur);
            window.addEventListener('pagehide', this.handlePageHide);
            window.addEventListener('pageshow', this.handlePageShow);
            window.addEventListener('online', this.handleOnline);
            this.heartbeatTimer = window.setInterval(() => this.flushHeartbeat(), this.heartbeatMs);
            this.log('page_enter', `进入页面：${this.pageKey}`, { page_key: this.pageKey });
            this.flushSoon();
            return this;
        }

        stop() {
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
            window.removeEventListener('online', this.handleOnline);
            if (this.flushTimer) {
                window.clearTimeout(this.flushTimer);
                this.flushTimer = null;
            }
            if (this.heartbeatTimer) {
                window.clearInterval(this.heartbeatTimer);
                this.heartbeatTimer = null;
            }
            this.flushHeartbeat(true);
        }

        handleActivity() {
            this.lastInteractionAt = Date.now();
        }

        handleVisibilityChange() {
            const hidden = document.visibilityState === 'hidden';
            this.log(
                'page_visibility',
                hidden ? '页面切到后台' : '页面回到前台',
                { visibility_state: hidden ? 'hidden' : 'visible' }
            );
            this.flushSoon();
        }

        handleFocus() {
            this.log('page_focus', '页面获得焦点', { focused: true });
            this.flushSoon();
        }

        handleBlur() {
            this.log('page_focus', '页面失去焦点', { focused: false });
            this.flushSoon();
        }

        handlePageHide() {
            this.stop();
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
            this.log(
                normalized ? 'ai_panel_open' : 'ai_panel_close',
                normalized ? '打开 AI 助手面板' : '关闭 AI 助手面板',
                { open: normalized },
                'ai_chat'
            );
            this.flushSoon(300);
        }

        buildHeartbeatEvent() {
            return {
                action_type: 'presence_heartbeat',
                summary_text: '',
                page_key: this.pageKey,
                payload: {
                    visibility_state: document.visibilityState === 'hidden' ? 'hidden' : 'visible',
                    focused: document.hasFocus(),
                    idle_seconds: Math.max(0, Math.round((Date.now() - this.lastInteractionAt) / 1000)),
                    ai_panel_open: this.aiPanelOpen,
                },
            };
        }

        flushHeartbeat(sync = false) {
            if (!this.classOfferingId) {
                return;
            }
            this.enqueueEvent(this.buildHeartbeatEvent());
            this.flush({ sync });
        }

        flushSoon(delay = 2200) {
            if (this.flushTimer || this.paused || !this.started) return;
            const wait = Math.max(delay, this.retryAt - Date.now(), 0);
            this.flushTimer = window.setTimeout(() => {
                this.flushTimer = null;
                this.flush();
            }, wait);
        }

        prepareBatch() {
            if (this.pendingBatch) return this.pendingBatch;
            if (!this.events.length) return null;
            const nonce = window.crypto?.randomUUID?.() || `${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
            const id = `b1_${Date.now()}_${nonce}`;
            this.pendingBatch = { body: {
                batch_id: id, user_key: this.userKey || null,
                page_key: this.pageKey, events: this.events.splice(0, Math.min(20, this.maxBatchSize)),
            } };
            this.persist();
            return this.pendingBatch;
        }

        async flush({ sync = false } = {}) {
            if (!this.classOfferingId || this.paused) return;
            const pending = this.prepareBatch();
            if (!pending) return;
            const body = JSON.stringify(pending.body);
            if (sync) {
                // A beacon is an unacknowledged copy, including when fetch is already in flight.
                // Retain the exact batch in sessionStorage until a response confirms durable intake.
                this.persist();
                if (navigator.sendBeacon) navigator.sendBeacon(this.endpoint, new Blob([body], { type: 'application/json' }));
                return;
            }
            if (this.flushing) return;
            if (Date.now() < this.retryAt) {
                this.flushSoon();
                return;
            }
            this.flushing = true;
            const controller = new AbortController();
            const timeout = window.setTimeout(() => controller.abort(), this.requestTimeoutMs);
            let retryAfterMs = 0;
            try {
                const response = await fetch(this.endpoint, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin', body, signal: controller.signal,
                });
                const retryHeader = response.headers.get('Retry-After');
                if (retryHeader) {
                    const seconds = Number(retryHeader);
                    retryAfterMs = Number.isFinite(seconds) ? seconds * 1000 : Math.max(0, Date.parse(retryHeader) - Date.now());
                }
                if (!response.ok) {
                    if (response.status < 500 && response.status !== 429 && response.status !== 408) this.paused = true;
                    throw new Error(`HTTP ${response.status}`);
                }
                const receipt = await response.json();
                if (receipt.status !== 'success' || receipt.degraded || !['durable_queued', 'synced', 'noop'].includes(receipt.write_mode)) {
                    throw new Error('Behavior intake was not confirmed');
                }
                if (receipt.batch_id && receipt.batch_id !== pending.body.batch_id) throw new Error('Behavior receipt mismatch');
                // A resumed bfcache page may have restored a newer shared-tab batch.
                if (this.pendingBatch?.body.batch_id !== pending.body.batch_id) return;
                this.pendingBatch = null;
                this.retryAttempt = 0;
                this.retryAt = 0;
                this.persist();
            } catch (error) {
                this.retryAttempt += 1;
                const backoff = Math.min(60000, 1000 * (2 ** Math.min(this.retryAttempt, 6)));
                this.retryAt = Date.now() + Math.max(retryAfterMs, backoff) + Math.random() * 1000;
                this.persist();
                console.warn('behavior flush deferred', error);
            } finally {
                window.clearTimeout(timeout);
                this.flushing = false;
                if (this.pendingBatch || this.events.length) this.flushSoon(500);
            }
        }
    }

    window.BehaviorTracker = BehaviorTracker;
})();

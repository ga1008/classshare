(function () {
    class BehaviorTracker {
        constructor(options = {}) {
            this.classOfferingId = Number(options.classOfferingId || 0);
            this.pageKey = String(options.pageKey || 'page').trim() || 'page';
            this.endpoint = options.endpoint || `/api/classrooms/${this.classOfferingId}/behavior/batch`;
            this.heartbeatMs = Number(options.heartbeatMs || 60000);
            this.maxBatchSize = Number(options.maxBatchSize || 12);
            this.maxQueuedEvents = Number(options.maxQueuedEvents || 48);
            this.events = [];
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
        }

        isPresenceEvent(actionType) {
            return ['presence_heartbeat', 'page_presence', 'heartbeat'].includes(String(actionType || ''));
        }

        isLowPriorityEvent(actionType) {
            return this.isPresenceEvent(actionType) || ['page_focus', 'page_visibility'].includes(String(actionType || ''));
        }

        enqueueEvent(event) {
            if (!event || !this.classOfferingId) {
                return;
            }

            if (this.isPresenceEvent(event.action_type)) {
                for (let index = this.events.length - 1; index >= 0; index -= 1) {
                    if (this.isPresenceEvent(this.events[index]?.action_type)) {
                        this.events[index] = event;
                        return;
                    }
                }
            }

            const lastEvent = this.events[this.events.length - 1];
            if (
                lastEvent
                && this.isLowPriorityEvent(lastEvent.action_type)
                && lastEvent.action_type === event.action_type
                && lastEvent.page_key === event.page_key
                && lastEvent.summary_text === event.summary_text
                && JSON.stringify(lastEvent.payload || {}) === JSON.stringify(event.payload || {})
            ) {
                this.events[this.events.length - 1] = event;
                return;
            }

            this.events.push(event);
            this.trimQueue();
        }

        trimQueue() {
            if (this.events.length <= this.maxQueuedEvents) {
                return;
            }

            for (let index = 0; index < this.events.length && this.events.length > this.maxQueuedEvents; index += 1) {
                if (!this.isLowPriorityEvent(this.events[index]?.action_type)) {
                    continue;
                }
                this.events.splice(index, 1);
                index -= 1;
            }

            if (this.events.length > this.maxQueuedEvents) {
                this.events.splice(0, this.events.length - this.maxQueuedEvents);
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
            this.heartbeatTimer = window.setInterval(() => this.flushHeartbeat(), this.heartbeatMs);
            this.log('page_enter', `进入页面：${this.pageKey}`, { page_key: this.pageKey });
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

        flushSoon(delay = 1500) {
            if (this.flushTimer) {
                return;
            }
            this.flushTimer = window.setTimeout(() => {
                this.flushTimer = null;
                this.flush();
            }, delay);
        }

        flush({ sync = false } = {}) {
            if (!this.classOfferingId || !this.events.length) {
                return;
            }
            if (this.flushing && !sync) {
                return;
            }

            const batch = this.events.splice(0, this.maxBatchSize);
            const body = JSON.stringify({
                page_key: this.pageKey,
                events: batch,
            });

            if (sync && navigator.sendBeacon) {
                const blob = new Blob([body], { type: 'application/json' });
                navigator.sendBeacon(this.endpoint, blob);
                return;
            }

            this.flushing = true;
            fetch(this.endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body,
                keepalive: sync,
            }).then((response) => {
                if (!response.ok && response.status >= 500) {
                    throw new Error(`HTTP ${response.status}`);
                }
            }).catch((error) => {
                console.warn('behavior flush failed', error);
                this.events = batch.concat(this.events);
                this.trimQueue();
            }).finally(() => {
                this.flushing = false;
                if (this.events.length) {
                    this.flushSoon(300);
                }
            });
        }
    }

    window.BehaviorTracker = BehaviorTracker;
})();

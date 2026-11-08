(function () {
    const MAX_PRESENCE_INTERVAL_MS = 90000;
    const PRESENCE_IDLE_MS = 150000;
    const MAX_BUFFER_CHARACTERS = 128 * 1024;
    const MAX_BUFFER_BYTES = 256 * 1024;
    const MAX_EVENT_BYTES = 16 * 1024;
    const MAX_BATCH_BYTES = 48 * 1024;
    const MAX_DIAGNOSTICS = 64;
    const NORMAL_DIAGNOSTIC_LIMIT = 32; // Reserve room for archive-release evidence.
    const MAX_RAW_BATCHES = 16;
    const MAX_RAW_DIAGNOSTIC_BATCHES = 8;
    const MAX_ARCHIVE_CHARACTERS = 128 * 1024;
    const MAX_ARCHIVE_SUMMARIES = 64;
    const TERMINAL_BATCH_ERRORS = new Set([
        'batch_payload_conflict', 'batch_expired', 'batch_timestamp_invalid',
        'batch_payload_invalid', 'batch_payload_too_large', 'classroom_unavailable', 'actor_unavailable',
    ]);

    function presenceNow() {
        const clock = window.performance;
        return Number.isFinite(clock?.timeOrigin) ? Math.floor(clock.timeOrigin + clock.now()) : Date.now();
    }

    function boundedOption(value, fallback, minimum, maximum) {
        const number = Number(value);
        return Number.isFinite(number) && number > 0 ? Math.max(minimum, Math.min(maximum, number)) : fallback;
    }

    function jsonSize(value) {
        const json = JSON.stringify(value);
        return { characters: json.length, bytes: new Blob([json]).size };
    }

    function payloadFingerprint(value) {
        // A local correlation fingerprint, not a cryptographic authenticity proof.
        const text = JSON.stringify(value);
        let first = 2166136261, second = 2246822519;
        for (let index = 0; index < text.length; index += 1) {
            const code = text.charCodeAt(index);
            first = Math.imul(first ^ code, 16777619);
            second = Math.imul(second ^ code, 3266489917);
        }
        return `${(first >>> 0).toString(16).padStart(8, '0')}${(second >>> 0).toString(16).padStart(8, '0')}:${text.length}`;
    }

    function diagnosticDetails(value) {
        const result = {};
        const batch = String(value.affected_batch_id || '');
        if (batch.length <= 96 && /^b1_[0-9]{10,16}_[A-Za-z0-9_-]+$/.test(batch)) result.affected_batch_id = batch;
        for (const [key, maximum, minimum] of [['event_count', 512, 0], ['occurrence_count', 1000000, 1], ['affected_batch_count', 1000000, 0]]) {
            if (value[key] !== undefined && Number.isFinite(Number(value[key]))) {
                result[key] = Math.max(minimum, Math.min(maximum, Math.floor(Number(value[key]))));
            }
        }
        const start = value.interval_start_ms, end = value.interval_end_ms;
        if (Number.isSafeInteger(start) && Number.isSafeInteger(end) && start > 0 && start <= end && end <= 4102444800000) {
            result.interval_start_ms = start;
            result.interval_end_ms = end;
        }
        return result;
    }

    class BehaviorTracker {
        constructor(options = {}) {
            this.classOfferingId = Number(options.classOfferingId || 0);
            this.pageKey = String(options.pageKey || 'page').trim() || 'page';
            this.endpoint = options.endpoint || `/api/classrooms/${this.classOfferingId}/behavior/batch`;
            this.heartbeatMs = boundedOption(options.heartbeatMs, 60000, 15000, 75000);
            this.maxBatchSize = Math.floor(boundedOption(options.maxBatchSize, 16, 1, 20));
            this.userKey = String(options.userKey || '').trim();
            this.storageNamespace = String(options.storageNamespace || '');
            const validNamespace = !this.storageNamespace || /^[a-z0-9_-]{1,64}$/.test(this.storageNamespace);
            this.storageKey = this.userKey && validNamespace ? `lanshare:behavior:v1:${this.userKey}:${this.classOfferingId}${this.storageNamespace ? `:${this.storageNamespace}` : ''}` : null;
            this.onReceipt = typeof options.onReceipt === 'function' ? options.onReceipt : null;
            this.diagnosticsEndpoint = options.diagnosticsEndpoint || `/api/classrooms/${this.classOfferingId}/behavior/diagnostics`;
            this.events = [];
            this.pendingBatch = null;
            this.diagnostics = [];
            this.quarantinedBatches = [];
            this.quarantinedDiagnostics = [];
            this.archiveSummaries = [];
            this.archiveTotals = { batches: 0, diagnostics: 0, events: 0, summaries_retired: 0, first_observed_ms: null, last_observed_ms: null };
            this.diagnosticOverflow = { occurrence_count: 0, affected_batch_count: 0, first_observed_ms: null, last_observed_ms: null };
            this.blockedDiagnosticGroups = [];
            this.eventsQuarantined = false;
            this.identityArchiveBatch = null;
            this.bufferBlocked = false;
            this.archiveBlocked = false;
            this.queuedCharacters = 0;
            this.queuedBytes = 0;
            this.pendingCharacters = 0;
            this.pendingBytes = 0;
            this.eventSizes = new WeakMap();
            this.normalBatchesSinceDiagnostic = 0;
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
            this.requestTimeoutMs = boundedOption(options.requestTimeoutMs, 12000, 3000, 30000);
            this.maxQueuedEvents = Math.floor(boundedOption(options.maxQueuedEvents, 512, 48, 512));
            this.flushTimer = null;
            this.storageWriteTimer = null;
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
            this.restore({ freshDocument: true });
            if (!validNamespace) this.recordDiagnostic('client_namespace_invalid');
        }

        restore({ freshDocument = false } = {}) {
            if (!this.storageKey) return;
            if (this.paused && !freshDocument) return;
            try {
                const raw = window.sessionStorage.getItem(this.storageKey);
                if (!raw) {
                    if (this.quarantinedBatches.length || this.quarantinedDiagnostics.length || this.eventsQuarantined) {
                        // External storage loss is not evidence that an
                        // unacknowledged archive was reconciled elsewhere.
                        this.recordDiagnostic('client_recovery_unavailable');
                        this.refreshCapacity();
                        return;
                    }
                    const wasSuspended = this.isCollectionSuspended();
                    this.events = [];
                    this.pendingBatch = null;
                    this.diagnostics = [];
                    this.blockedDiagnosticGroups = [];
                    this.diagnosticOverflow = { occurrence_count: 0, affected_batch_count: 0, first_observed_ms: null, last_observed_ms: null };
                    this.bufferBlocked = this.archiveBlocked = false;
                    this.queuedCharacters = this.queuedBytes = this.pendingCharacters = this.pendingBytes = 0;
                    this.retryAt = this.diagnosticRetryAt = this.retryAfterAt = 0;
                    this.transportState = 'healthy';
                    this.updateSuspension(wasSuspended);
                    return;
                }
                const state = JSON.parse(raw);
                if (state?.userKey !== this.userKey) return;
                this.events = Array.isArray(state.events) ? state.events : [];
                this.pendingBatch = state.pendingBatch?.body?.user_key === this.userKey ? state.pendingBatch : null;
                this.diagnostics = Array.isArray(state.diagnostics) ? state.diagnostics : [];
                this.quarantinedBatches = Array.isArray(state.quarantinedBatches) ? state.quarantinedBatches : [];
                this.quarantinedDiagnostics = Array.isArray(state.quarantinedDiagnostics) ? state.quarantinedDiagnostics : [];
                this.archiveSummaries = Array.isArray(state.archiveSummaries) ? state.archiveSummaries.slice(-MAX_ARCHIVE_SUMMARIES) : [];
                this.archiveTotals = { ...this.archiveTotals, ...(state.archiveTotals || {}) };
                this.diagnosticOverflow = { ...this.diagnosticOverflow, ...(state.diagnosticOverflow || {}) };
                this.blockedDiagnosticGroups = Array.isArray(state.blockedDiagnosticGroups) ? state.blockedDiagnosticGroups : [];
                this.eventsQuarantined = state.eventsQuarantined === true;
                this.identityArchiveBatch = state.identityArchiveBatch?.body?.user_key === this.userKey ? state.identityArchiveBatch : null;
                this.bufferBlocked = state.bufferBlocked === true;
                this.recountBuffer();
                this.transportState = ['auth_required', 'identity_changed', 'blocked', 'retrying'].includes(state.transportState)
                    ? state.transportState : 'healthy';
                this.suspendedAt = Number.isFinite(state.suspendedAt) ? state.suspendedAt : null;
                // A freshly rendered page supplies the authenticated identity. A bfcache page does not.
                if (freshDocument && this.transportState === 'identity_changed') {
                    this.transportState = 'healthy';
                    this.suspendedAt = null;
                }
                this.paused = this.transportState === 'identity_changed';
                this.refreshCapacity();
            } catch (error) {
                console.warn('behavior recovery unavailable', error);
                this.recordDiagnostic('client_recovery_unavailable');
            }
        }

        persist() {
            if (!this.storageKey) return;
            if (this.storageWriteTimer) {
                window.clearTimeout(this.storageWriteTimer);
                this.storageWriteTimer = null;
            }
            try {
                if (!this.events.length && !this.pendingBatch && !this.diagnostics.length
                    && !this.quarantinedBatches.length && !this.quarantinedDiagnostics.length
                    && !this.archiveSummaries.length && !this.diagnosticOverflow.occurrence_count
                    && !this.archiveTotals.batches && !this.archiveTotals.diagnostics) {
                    window.sessionStorage.removeItem(this.storageKey);
                } else {
                    window.sessionStorage.setItem(this.storageKey, JSON.stringify({
                        userKey: this.userKey, events: this.events, pendingBatch: this.pendingBatch,
                        diagnostics: this.diagnostics, quarantinedBatches: this.quarantinedBatches,
                        quarantinedDiagnostics: this.quarantinedDiagnostics,
                        ...(this.archiveSummaries.length ? { archiveSummaries: this.archiveSummaries } : {}),
                        ...(this.archiveTotals.batches || this.archiveTotals.diagnostics ? { archiveTotals: this.archiveTotals } : {}),
                        ...(this.diagnosticOverflow.occurrence_count ? { diagnosticOverflow: this.diagnosticOverflow } : {}),
                        ...(this.blockedDiagnosticGroups.length ? { blockedDiagnosticGroups: this.blockedDiagnosticGroups } : {}),
                        ...(this.eventsQuarantined ? { eventsQuarantined: true } : {}),
                        ...(this.identityArchiveBatch ? { identityArchiveBatch: this.identityArchiveBatch } : {}),
                        ...(this.bufferBlocked ? { bufferBlocked: true } : {}),
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

        persistSoon() {
            if (!this.storageKey || this.storageWriteTimer) return;
            this.storageWriteTimer = window.setTimeout(() => {
                this.storageWriteTimer = null;
                this.persist();
            }, 100);
        }

        eventSize(event) {
            let size = this.eventSizes.get(event);
            if (!size) {
                size = jsonSize(event);
                this.eventSizes.set(event, size);
            }
            return size;
        }

        recountBuffer() {
            this.queuedCharacters = 0;
            this.queuedBytes = 0;
            for (const event of this.events) {
                const size = this.eventSize(event);
                this.queuedCharacters += size.characters;
                this.queuedBytes += size.bytes;
            }
            const pendingSize = this.pendingBatch ? jsonSize(this.pendingBatch.body) : { characters: 0, bytes: 0 };
            this.pendingCharacters = pendingSize.characters;
            this.pendingBytes = pendingSize.bytes;
        }

        isTransportSuspended() {
            return ['auth_required', 'identity_changed', 'blocked'].includes(this.transportState);
        }

        isCollectionSuspended() {
            return this.isTransportSuspended() || this.bufferBlocked || this.archiveBlocked || this.eventsQuarantined;
        }

        setTransportState(state, errorCode = null) {
            const wasSuspended = this.isCollectionSuspended();
            this.transportState = state;
            this.paused = state === 'identity_changed';
            this.updateSuspension(wasSuspended);
            this.emitStatus(errorCode);
        }

        updateSuspension(wasSuspended) {
            const now = presenceNow();
            if (this.isCollectionSuspended() && this.suspendedAt === null) {
                this.suspendedAt = this.presenceAt > 0 ? Math.min(now, this.presenceAt) : now;
            }
            if (wasSuspended && !this.isCollectionSuspended()) {
                this.recordDiagnostic('collection_resumed', { interval_start_ms: this.suspendedAt, interval_end_ms: now });
                this.suspendedAt = null;
                this.presenceAt = now;
                this.lastInteractionAt = now;
                this.presenceState = this.readPresenceState();
                if (this.started) this.enqueueEvent(this.buildHeartbeatEvent(now));
            }
        }

        emitStatus(errorCode = null) {
            window.dispatchEvent(new CustomEvent('behavior:status', { detail: {
                state: this.transportState, error_code: errorCode, queued: this.events.length,
                diagnostic_count: this.diagnostics.length, quarantined_count: this.quarantinedBatches.length,
                buffer_blocked: this.bufferBlocked, archive_blocked: this.archiveBlocked,
                summarized_batch_count: this.archiveTotals.batches,
            } }));
        }

        recordDiagnostic(errorCode, details = {}, critical = false, deduplicate = true) {
            // Retries describe one incident, rather than creating a diagnostic for every attempt.
            errorCode = /^[a-z][a-z0-9_]{0,63}$/.test(String(errorCode)) ? String(errorCode) : 'client_diagnostic_code_invalid';
            details = diagnosticDetails(details);
            const affectedBatch = details.affected_batch_id || null;
            const blocked = new Set(this.blockedDiagnosticGroups.flatMap(group => group.ids));
            const existing = this.diagnostics.find(item => !blocked.has(item.diagnostic_id) && item.error_code === errorCode
                && (item.affected_batch_id || null) === affectedBatch
                && item.interval_start_ms === details.interval_start_ms && item.interval_end_ms === details.interval_end_ms);
            if (existing && deduplicate) return existing;
            const limit = critical ? MAX_DIAGNOSTICS : NORMAL_DIAGNOSTIC_LIMIT;
            if (this.diagnostics.length >= limit) {
                // This is a bounded live delta. An emitted diagnostic is never
                // mutated while its response may still be in flight.
                const count = this.diagnosticOverflow;
                const occurrences = Math.floor(boundedOption(details.occurrence_count, 1, 1, 1000000));
                const affected = Math.min(occurrences, Math.max(0, Number(details.affected_batch_count ?? Number(Boolean(affectedBatch))) || 0));
                count.occurrence_count = Math.min(Number.MAX_SAFE_INTEGER, count.occurrence_count + occurrences);
                count.affected_batch_count = Math.min(Number.MAX_SAFE_INTEGER, count.affected_batch_count + affected);
                const now = presenceNow();
                count.first_observed_ms ??= now;
                count.last_observed_ms = now;
                this.persistSoon();
                return null;
            }
            const nonce = window.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
            const item = { diagnostic_id: `d1_${Date.now()}_${nonce}`, error_code: errorCode,
                observed_ms: presenceNow(), ...details };
            this.diagnostics.push(item);
            this.persistSoon();
            return item;
        }

        materializeDiagnosticOverflow() {
            const count = this.diagnosticOverflow;
            if (!count.occurrence_count || this.diagnostics.length >= NORMAL_DIAGNOSTIC_LIMIT) return;
            const occurrences = Math.min(1000000, count.occurrence_count);
            const affected = Math.min(occurrences, count.affected_batch_count);
            const report = this.recordDiagnostic('client_diagnostic_overflow', {
                occurrence_count: occurrences, affected_batch_count: affected,
                interval_start_ms: count.first_observed_ms, interval_end_ms: count.last_observed_ms,
            }, false, false);
            if (!report) return;
            count.occurrence_count -= occurrences;
            count.affected_batch_count -= affected;
            if (!count.occurrence_count) count.first_observed_ms = count.last_observed_ms = null;
        }

        batchDiagnostic(errorCode, body, critical = false) {
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
            }, critical);
        }

        quarantineBatch(body, errorCode) {
            if (!body) return false;
            this.compactAcknowledgedArchives(JSON.stringify(body).length);
            if (!this.quarantinedBatches.some(item => item.body.batch_id === body.batch_id)) {
                const holder = this.pendingBatch?.body.batch_id === body.batch_id ? this.pendingBatch
                    : this.identityArchiveBatch?.body.batch_id === body.batch_id ? this.identityArchiveBatch : null;
                const reportId = holder?.diagnostic_id || this.batchDiagnostic(errorCode, body, true)?.diagnostic_id || null;
                if (holder) holder.diagnostic_id = reportId;
                const record = { body, error_code: errorCode, observed_ms: presenceNow(),
                    diagnostic_id: reportId, diagnostic_acknowledged: holder?.diagnostic_acknowledged === true };
                if (!this.archiveHasRoom('batch', body)) {
                    if (record.diagnostic_acknowledged) {
                        this.appendArchiveSummary(record, 'batch');
                        this.recordDiagnostic('client_archive_compacted', { occurrence_count: 1, affected_batch_count: 1 }, false, false);
                        if (this.pendingBatch?.body.batch_id === body.batch_id) this.pendingBatch = null;
                        this.recountBuffer();
                        this.refreshCapacity();
                        return true;
                    }
                    if (this.pendingBatch?.body.batch_id === body.batch_id) this.pendingBatch.terminal_error_code = errorCode;
                    this.refreshCapacity();
                    return false;
                }
                this.quarantinedBatches.push(record);
            }
            if (this.pendingBatch?.body.batch_id === body.batch_id) this.pendingBatch = null;
            this.recountBuffer();
            this.refreshCapacity();
            return true;
        }

        archiveCharacters() {
            return this.quarantinedBatches.reduce((total, item) => total + JSON.stringify(item.body).length, 0)
                + this.quarantinedDiagnostics.reduce((total, item) => total + JSON.stringify(item.diagnostics).length, 0);
        }

        archiveHasRoom(kind, value) {
            const count = kind === 'batch' ? this.quarantinedBatches.length : this.quarantinedDiagnostics.length;
            const limit = kind === 'batch' ? MAX_RAW_BATCHES : MAX_RAW_DIAGNOSTIC_BATCHES;
            return count < limit && this.archiveCharacters() + JSON.stringify(value).length <= MAX_ARCHIVE_CHARACTERS;
        }

        compactAcknowledgedArchives(requiredCharacters = 0) {
            let compacted = 0, affectedBatches = 0;
            for (const [kind, records, limit] of [['batch', this.quarantinedBatches, MAX_RAW_BATCHES],
                ['diagnostics', this.quarantinedDiagnostics, MAX_RAW_DIAGNOSTIC_BATCHES]]) {
                while (records.length >= limit || this.archiveCharacters() + requiredCharacters > MAX_ARCHIVE_CHARACTERS) {
                    const index = records.findIndex(item => item.diagnostic_acknowledged);
                    if (index < 0) break;
                    const [record] = records.splice(index, 1);
                    this.appendArchiveSummary(record, kind);
                    compacted += 1;
                    if (kind === 'batch') affectedBatches += 1;
                }
            }
            if (compacted) this.recordDiagnostic('client_archive_compacted', {
                occurrence_count: compacted, affected_batch_count: affectedBatches,
            }, false, false);
        }

        appendArchiveSummary(record, kind) {
            const value = kind === 'batch' ? record.body : record.diagnostics;
            const eventCount = kind === 'batch' ? (record.body.events?.length || 0) : record.diagnostics.length;
            this.archiveSummaries.push({ kind, batch_id: record.body?.batch_id || null,
                diagnostic_id: record.diagnostic_id, error_code: record.error_code,
                observed_ms: record.observed_ms, event_count: eventCount,
                ...(kind === 'diagnostics' ? { source_diagnostic_ids: record.diagnostics.map(item => String(item.diagnostic_id).slice(0, 96)) } : {}),
                payload_fingerprint: payloadFingerprint(value) });
            this.archiveTotals[kind === 'batch' ? 'batches' : 'diagnostics'] += 1;
            this.archiveTotals.events += eventCount;
            this.archiveTotals.first_observed_ms = Math.min(this.archiveTotals.first_observed_ms ?? record.observed_ms, record.observed_ms);
            this.archiveTotals.last_observed_ms = Math.max(this.archiveTotals.last_observed_ms ?? record.observed_ms, record.observed_ms);
            while (this.archiveSummaries.length > MAX_ARCHIVE_SUMMARIES) {
                this.archiveSummaries.shift();
                this.archiveTotals.summaries_retired += 1;
            }
        }

        ensureArchiveDiagnostics() {
            if (this.pendingBatch?.terminal_error_code && !this.pendingBatch.diagnostic_id) {
                this.pendingBatch.diagnostic_id = this.batchDiagnostic(this.pendingBatch.terminal_error_code, this.pendingBatch.body, true)?.diagnostic_id || null;
            }
            for (const record of this.quarantinedBatches) {
                if (!record.diagnostic_id) record.diagnostic_id = this.batchDiagnostic(record.error_code, record.body, true)?.diagnostic_id || null;
            }
            for (const record of this.quarantinedDiagnostics) {
                if (!record.diagnostic_id) record.diagnostic_id = this.recordDiagnostic(record.error_code, {
                    occurrence_count: record.diagnostics.length,
                    interval_start_ms: record.observed_ms, interval_end_ms: record.observed_ms,
                }, true)?.diagnostic_id || null;
            }
            for (const group of this.blockedDiagnosticGroups) {
                if (!group.diagnostic_id) group.diagnostic_id = this.recordDiagnostic(group.error_code, {
                    occurrence_count: group.ids.length,
                    interval_start_ms: group.observed_ms, interval_end_ms: group.observed_ms,
                }, true)?.diagnostic_id || null;
            }
        }

        archiveRejectedDiagnostics(items, errorCode) {
            this.compactAcknowledgedArchives(JSON.stringify(items).length);
            const ids = new Set(items.map(item => item.diagnostic_id));
            // A rejected mixed packet may contain valid archive-release
            // reports too. Recreate those reports with fresh IDs instead of
            // leaving their raw archives waiting for an impossible ACK.
            for (const record of [...this.quarantinedBatches, ...this.quarantinedDiagnostics, this.pendingBatch, this.identityArchiveBatch]) {
                if (record && !record.diagnostic_acknowledged && ids.has(record.diagnostic_id)) record.diagnostic_id = null;
            }
            if (this.archiveHasRoom('diagnostics', items)) {
                const record = { diagnostics: items, error_code: errorCode, observed_ms: presenceNow(),
                    diagnostic_id: null, diagnostic_acknowledged: false };
                this.quarantinedDiagnostics.push(record);
                this.diagnostics = this.diagnostics.filter(item => !ids.has(item.diagnostic_id));
                this.blockedDiagnosticGroups = this.blockedDiagnosticGroups.filter(group => !group.ids.some(id => ids.has(id)));
                this.ensureArchiveDiagnostics();
            } else if (!this.blockedDiagnosticGroups.some(group => group.ids.some(id => ids.has(id)))) {
                this.blockedDiagnosticGroups.push({ ids: [...ids], error_code: errorCode, observed_ms: presenceNow(), diagnostic_id: null });
                this.ensureArchiveDiagnostics();
            }
            this.refreshCapacity();
        }

        acknowledgeDiagnostics(ids) {
            for (const record of [...this.quarantinedBatches, ...this.quarantinedDiagnostics]) {
                if (ids.has(record.diagnostic_id)) record.diagnostic_acknowledged = true;
            }
            for (const holder of [this.pendingBatch, this.identityArchiveBatch]) {
                if (holder && ids.has(holder.diagnostic_id)) holder.diagnostic_acknowledged = true;
            }
            for (const group of [...this.blockedDiagnosticGroups]) {
                if (!ids.has(group.diagnostic_id)) continue;
                const items = this.diagnostics.filter(item => group.ids.includes(item.diagnostic_id));
                if (items.length) {
                    this.appendArchiveSummary({ diagnostics: items, diagnostic_id: group.diagnostic_id,
                        error_code: group.error_code, observed_ms: group.observed_ms }, 'diagnostics');
                    for (const item of items) ids.add(item.diagnostic_id);
                    this.recordDiagnostic('client_archive_compacted', { occurrence_count: 1, affected_batch_count: 0 }, false, false);
                }
            }
            this.diagnostics = this.diagnostics.filter(item => !ids.has(item.diagnostic_id));
            this.blockedDiagnosticGroups = this.blockedDiagnosticGroups
                .map(group => ({ ...group, ids: group.ids.filter(id => !ids.has(id)) })).filter(group => group.ids.length);
            this.compactAcknowledgedArchives();
            for (const group of [...this.blockedDiagnosticGroups]) {
                const items = this.diagnostics.filter(item => group.ids.includes(item.diagnostic_id));
                if (items.length && this.archiveHasRoom('diagnostics', items)) this.archiveRejectedDiagnostics(items, group.error_code);
            }
            if (this.pendingBatch?.terminal_error_code) this.quarantineBatch(this.pendingBatch.body, this.pendingBatch.terminal_error_code);
            this.archiveIsolatedEvents();
            this.ensureArchiveDiagnostics();
            this.materializeDiagnosticOverflow();
            this.refreshCapacity();
        }

        refreshCapacity() {
            const wasSuspended = this.isCollectionSuspended();
            this.compactAcknowledgedArchives();
            this.archiveBlocked = this.quarantinedBatches.length >= MAX_RAW_BATCHES
                || this.quarantinedDiagnostics.length >= MAX_RAW_DIAGNOSTIC_BATCHES
                || this.archiveCharacters() >= MAX_ARCHIVE_CHARACTERS
                || Boolean(this.pendingBatch?.terminal_error_code) || this.blockedDiagnosticGroups.length > 0
                || this.eventsQuarantined;
            if (this.bufferBlocked && this.events.length < this.maxQueuedEvents * 0.75
                && this.queuedCharacters + this.pendingCharacters < MAX_BUFFER_CHARACTERS * 0.75
                && this.queuedBytes + this.pendingBytes < MAX_BUFFER_BYTES * 0.75) this.bufferBlocked = false;
            this.updateSuspension(wasSuspended);
        }

        isolateIdentity() {
            this.recordDiagnostic('identity_changed');
            this.setTransportState('identity_changed', 'identity_changed');
            this.quarantineBatch(this.pendingBatch?.body, 'identity_changed');
            this.eventsQuarantined = this.events.length > 0;
            this.archiveIsolatedEvents();
            this.persist();
        }

        archiveIsolatedEvents() {
            while (this.eventsQuarantined && this.events.length) {
                if (!this.identityArchiveBatch) {
                    const items = this.takeBatchEvents(false);
                    this.identityArchiveBatch = { body: this.makeBatchBody(items) };
                }
                if (!this.quarantineBatch(this.identityArchiveBatch.body, 'identity_changed')) break;
                this.events.splice(0, this.identityArchiveBatch.body.events.length);
                this.identityArchiveBatch = null;
                this.recountBuffer();
            }
            if (!this.events.length) this.eventsQuarantined = false;
        }

        enqueueEvent(event) {
            if (!event || !this.classOfferingId || this.isCollectionSuspended()) return false;
            let size;
            try { size = this.eventSize(event); }
            catch {
                this.recordDiagnostic('client_event_invalid', { event_count: 1 });
                return false;
            }
            if (size.bytes > MAX_EVENT_BYTES) {
                this.recordDiagnostic('client_event_too_large', { event_count: 1 });
                return false;
            }
            // Preserve heartbeat/focus ordering and activity counts. Never trim an unacknowledged batch.
            if (this.events.length >= this.maxQueuedEvents
                || this.queuedCharacters + this.pendingCharacters + size.characters > MAX_BUFFER_CHARACTERS
                || this.queuedBytes + this.pendingBytes + size.bytes > MAX_BUFFER_BYTES) {
                window.dispatchEvent(new CustomEvent('behavior:backpressure', { detail: { queued: this.events.length } }));
                const wasSuspended = this.isCollectionSuspended();
                this.bufferBlocked = true;
                this.recordDiagnostic('client_buffer_full', { event_count: this.events.length,
                    ...(Number.isFinite(event.payload?.interval_start_ms) && Number.isFinite(event.payload?.interval_end_ms)
                        ? { interval_start_ms: event.payload.interval_start_ms, interval_end_ms: event.payload.interval_end_ms } : {}),
                }, true);
                this.updateSuspension(wasSuspended);
                this.emitStatus('client_buffer_full');
                this.persist();
                this.flushSoon();
                return false;
            }
            this.events.push(event);
            this.queuedCharacters += size.characters;
            this.queuedBytes += size.bytes;
            this.persistSoon();
            return true;
        }

        handleOnline() {
            if (this.paused) return;
            this.retryAt = Math.max(this.retryAfterAt, this.lastAttemptAt + 3000);
            this.flushSoon(1000 + Math.random() * 5000);
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
            this.scheduleHeartbeat();
            this.log('page_enter', `进入页面：${this.pageKey}`, { page_key: this.pageKey });
            if (!this.isCollectionSuspended()) this.enqueueEvent(this.buildHeartbeatEvent(this.presenceAt));
            this.flushSoon(2200 + Math.random() * 3000);
            return this;
        }

        scheduleHeartbeat() {
            if (!this.started || this.heartbeatTimer) return;
            const jitter = Math.min(5000, this.heartbeatMs * 0.15);
            const wait = Math.max(15000, Math.min(75000, this.heartbeatMs + (Math.random() * 2 - 1) * jitter));
            this.heartbeatTimer = window.setTimeout(() => {
                this.heartbeatTimer = null;
                if (!this.started) return;
                this.flushHeartbeat();
                this.scheduleHeartbeat();
            }, wait);
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
                window.clearTimeout(this.heartbeatTimer);
                this.heartbeatTimer = null;
            }
            this.flushHeartbeat(true);
            this.persist();
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

        flushSoon(delay = null) {
            if (this.paused || !this.started) return;
            if (delay === null) delay = 2200 + Math.random() * 3000;
            const readiness = [];
            if ((this.pendingBatch && !this.pendingBatch.terminal_error_code) || (this.events.length && !this.eventsQuarantined)) readiness.push(this.retryAt);
            if (this.diagnostics.length || this.diagnosticOverflow.occurrence_count || this.pendingBatch?.terminal_error_code) {
                readiness.push(Math.max(this.diagnosticRetryAt, this.retryAt));
            }
            if (!readiness.length) return;
            const wait = Math.max(delay, Math.min(...readiness) - Date.now(), 0);
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
            if (!this.events.length || this.eventsQuarantined) return null;
            this.pendingBatch = { body: this.makeBatchBody(this.takeBatchEvents()) };
            this.recountBuffer();
            this.persist();
            return this.pendingBatch;
        }

        takeBatchEvents(remove = true) {
            const items = [];
            let bytes = 0;
            for (const event of this.events.slice(0, this.maxBatchSize)) {
                const size = this.eventSize(event);
                if (items.length && bytes + size.bytes > MAX_BATCH_BYTES) break;
                items.push(event);
                bytes += size.bytes;
            }
            if (remove) this.events.splice(0, items.length);
            return items;
        }

        diagnosticItems() {
            const blocked = new Set(this.blockedDiagnosticGroups.flatMap(group => group.ids));
            const eligible = this.diagnostics.filter(item => !blocked.has(item.diagnostic_id));
            // A completely blocked legacy package is probed slowly. It never
            // consumes a growing retry task or discards its original content.
            return eligible.length ? eligible.slice(0, 20) : this.diagnostics.slice(0, 1);
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

        notifyReceipt(receipt, body) {
            if (!this.onReceipt) return;
            const failed = (error) => {
                this.recordDiagnostic('client_receipt_callback_failed');
                console.warn('behavior receipt consumer failed', error);
            };
            try {
                const result = this.onReceipt(receipt, body);
                if (result && typeof result.then === 'function') Promise.resolve(result).catch(failed);
            } catch (error) { failed(error); }
        }

        async flush({ sync = false } = {}) {
            if (!this.classOfferingId || this.paused) return;
            const behavior = this.prepareBatch();
            if (sync) {
                // A beacon is an unacknowledged copy, including when fetch is already in flight.
                // Retain the exact batch in sessionStorage until a response confirms durable intake.
                if (behavior && !behavior.terminal_error_code) {
                    this.persist();
                    this.sendUnconfirmed(behavior.body);
                }
                return;
            }
            if (this.flushing) return;
            this.ensureArchiveDiagnostics();
            this.materializeDiagnosticOverflow();
            const candidates = this.diagnosticItems();
            const diagnosticDue = Date.now() >= Math.max(this.diagnosticRetryAt, this.retryAt);
            const oldest = candidates.length ? Math.min(...candidates.map(item => item.observed_ms)) : Infinity;
            const chooseDiagnostic = candidates.length && diagnosticDue && (!behavior || behavior.terminal_error_code
                || (!this.isTransportSuspended() && (this.normalBatchesSinceDiagnostic >= 4 || presenceNow() - oldest >= 30000)));
            const pending = chooseDiagnostic || behavior?.terminal_error_code ? null : behavior;
            const diagnosticItems = chooseDiagnostic ? candidates : [];
            if (!pending && !diagnosticItems.length) return;
            if (Date.now() < (pending ? this.retryAt : Math.max(this.diagnosticRetryAt, this.retryAt))) {
                this.flushSoon();
                return;
            }
            this.flushing = true;
            this.lastAttemptAt = Date.now();
            this.persist(); // Both immutable business batches and diagnostic IDs precede network I/O.
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
                        const archived = this.quarantineBatch(pending.body, code);
                        this.retryAttempt = 0;
                        this.retryAt = 0;
                        if (['classroom_unavailable', 'actor_unavailable'].includes(code)) this.setTransportState('blocked', code);
                        else if (archived || !this.isTransportSuspended()) this.setTransportState('healthy', code);
                        this.persist();
                        return;
                    }
                    if (!pending && failure?.batch_disposition === 'quarantine'
                        && ['diagnostic_timestamp_invalid', 'diagnostic_interval_invalid', 'diagnostic_payload_invalid'].includes(code)) {
                        this.archiveRejectedDiagnostics(diagnosticItems, code);
                        this.diagnosticRetryAt = this.blockedDiagnosticGroups.length ? Date.now() + 60000 + Math.random() * 15000 : 0;
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
                    if (receipt.batch_id !== pending.body.batch_id) throw new Error('Behavior receipt mismatch');
                    // A resumed bfcache page may have restored a newer shared-tab batch.
                    if (this.pendingBatch?.body.batch_id !== pending.body.batch_id) return;
                    this.pendingBatch = null;
                    this.normalBatchesSinceDiagnostic += 1;
                    this.recountBuffer();
                    this.refreshCapacity();
                } else {
                    const accepted = new Set(receipt.accepted_diagnostic_ids || []);
                    if (!diagnosticItems.every(item => accepted.has(item.diagnostic_id))) throw new Error('Behavior diagnostic receipt mismatch');
                    this.acknowledgeDiagnostics(accepted);
                    this.normalBatchesSinceDiagnostic = 0;
                }
                this.retryAttempt = 0;
                this.retryAt = 0;
                this.retryAfterAt = 0;
                if (!pending) {
                    this.diagnosticRetryAt = 0;
                    this.diagnosticRetryAttempt = 0;
                }
                if (pending || !this.pendingBatch || this.pendingBatch.terminal_error_code || !this.isTransportSuspended()) this.setTransportState('healthy');
                this.persist();
                if (pending) this.notifyReceipt(receipt, pending.body);
            } catch (error) {
                if (this.paused) return;
                if (pending) this.retryAttempt += 1;
                else this.diagnosticRetryAttempt += 1;
                const attempt = pending ? this.retryAttempt : this.diagnosticRetryAttempt;
                const backoff = Math.min(60000, 1000 * (2 ** Math.min(attempt, 6)));
                const minimumWait = this.isTransportSuspended() ? 15000 : 0;
                const baseWait = Math.max(retryAfterMs, backoff, minimumWait);
                const nextAttemptAt = Date.now() + baseWait + Math.random() * Math.max(1000, baseWait * 0.3);
                if (pending || this.isTransportSuspended()) this.retryAt = nextAttemptAt;
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

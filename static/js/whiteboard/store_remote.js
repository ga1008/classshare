/**
 * 线上存储客户端：/api/materials/{material_id}/whiteboards
 * 分享/协作只声明签名（见方案 4.4），本次不实现。
 */

export class RemoteError extends Error {
    constructor(message, { status = 0, payload = null, cause = null } = {}) {
        super(message);
        this.name = 'RemoteError';
        this.status = status;
        this.payload = payload;
        this.cause = cause;
    }

    get isConflict() {
        return this.status === 409;
    }

    get isNetwork() {
        return this.status === 0;
    }
}

async function request(url, { method = 'GET', body, keepalive = false } = {}) {
    let response;
    try {
        response = await fetch(url, {
            method,
            credentials: 'same-origin',
            keepalive,
            headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
            // 已经序列化好的直接用，避免大 payload 被 stringify 两遍。
            body: body === undefined ? undefined : (typeof body === 'string' ? body : JSON.stringify(body)),
        });
    } catch (error) {
        throw new RemoteError('网络不可用', { status: 0, cause: error });
    }
    let payload = null;
    try {
        payload = await response.json();
    } catch {
        payload = null;
    }
    if (!response.ok) {
        const message = payload?.detail || payload?.message || `请求失败（${response.status}）`;
        throw new RemoteError(typeof message === 'string' ? message : '请求失败', { status: response.status, payload });
    }
    return payload || {};
}

/** 服务端行 → 本地板结构（不含本地专有字段）。 */
export function remoteToBoard(row, { withElements = false } = {}) {
    const loaded = withElements && Array.isArray(row.elements);
    return {
        id: String(row.board_key),
        name: String(row.name || ''),
        createdAt: row.created_at || row.updated_at,
        updatedAt: row.updated_at,
        viewport: row.viewport || undefined,
        elements: loaded ? row.elements : [],
        elementsLoaded: loaded,
        elementCount: Number(row.element_count || 0),
        remoteVersion: Number(row.version || 0),
        syncedAt: row.updated_at || null,
        dirty: false,
    };
}

export class RemoteStore {
    constructor(materialId) {
        this.base = `/api/materials/${encodeURIComponent(String(materialId))}/whiteboards`;
    }

    async list() {
        const data = await request(this.base);
        return Array.isArray(data.boards) ? data.boards : [];
    }

    async get(boardKey) {
        const data = await request(`${this.base}/${encodeURIComponent(boardKey)}`);
        return data.board || null;
    }

    /**
     * 保存整块板。`serialized` 是调用方已经序列化好的请求体（sync 那边量体积时就做过一次），
     * 传它可以省掉一次大字符串的重复构造；否则按字段现拼。
     */
    async upsert(boardKey, { serialized, name, viewport, elements, baseVersion, keepalive = false }) {
        const data = await request(`${this.base}/${encodeURIComponent(boardKey)}`, {
            method: 'PUT',
            keepalive,
            body: serialized ?? { name, viewport, elements, schema_version: 2, base_version: baseVersion },
        });
        return data.board || null;
    }

    async rename(boardKey, name) {
        const data = await request(`${this.base}/${encodeURIComponent(boardKey)}`, { method: 'PATCH', body: { name } });
        return data.board || null;
    }

    async remove(boardKey) {
        await request(`${this.base}/${encodeURIComponent(boardKey)}`, { method: 'DELETE' });
    }

    /**
     * 预留：生成分享链接（visibility=shared）。
     * @param {string} _boardKey
     * @returns {Promise<{shareToken:string, url:string}>}
     */
    async share(_boardKey) {
        throw new RemoteError('分享功能尚未开放', { status: 501 });
    }

    /**
     * 预留：协作订阅（visibility=collab，WebSocket）。
     * @param {string} _boardKey
     * @param {(op:object)=>void} _onOperation
     * @returns {() => void} 取消订阅
     */
    subscribe(_boardKey, _onOperation) {
        throw new RemoteError('协作功能尚未开放', { status: 501 });
    }
}

/**
 * 在线服务器监控大屏（超管专属）。
 *
 * 全部图表为手绘 SVG：趋势双折线、访问压力柱、状态码/内存双环、健康评分环。
 * 数据源：/api/manage/system/monitor/*，5 秒自动轮询（可暂停）。
 */

const REFRESH_INTERVAL_MS = 5000;
const SVG_NS = 'http://www.w3.org/2000/svg';

const COLORS = {
    accent: '#38bdf8',
    violet: '#a78bfa',
    good: '#34d399',
    warn: '#fbbf24',
    bad: '#fb7185',
    muted: '#8aa0c5',
    grid: 'rgba(96, 128, 190, 0.16)',
};

const STATUS_COLORS = {
    '2xx': COLORS.good,
    '3xx': COLORS.accent,
    '4xx': COLORS.warn,
    '5xx': COLORS.bad,
};

const state = {
    autoRefresh: true,
    timer: null,
    processes: [],
    procFilter: '',
    collapsedPids: new Set(),
    selfPid: 0,
    loading: false,
};

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, text = '') {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text) node.textContent = text;
    return node;
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[ch]);
}

function levelColor(percent) {
    if (percent >= 90) return COLORS.bad;
    if (percent >= 70) return COLORS.warn;
    return COLORS.accent;
}

function formatUptime(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (days > 0) return `${days}天${hours}时`;
    if (hours > 0) return `${hours}时${minutes}分`;
    return `${minutes}分${total % 60}秒`;
}

function formatTime(iso) {
    if (!iso) return '';
    try {
        return new Date(iso).toLocaleTimeString('zh-CN', { hour12: false });
    } catch (_) {
        return String(iso);
    }
}

async function apiGet(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

async function apiPost(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
}

/* ------------------------------------------------------------------ tiles */

function renderTiles(snapshot) {
    const resources = snapshot.resources || {};
    const traffic = snapshot.traffic || {};
    const connections = snapshot.connections || {};
    const cpu = resources.cpu || {};
    const memory = resources.memory || {};
    const disk = resources.disk || {};

    const tiles = [
        {
            label: 'CPU 占用',
            value: resources.resource_ok ? `${cpu.percent ?? 0}<small>%</small>` : '—',
            sub: cpu.load_avg?.length ? `负载 ${cpu.load_avg.join(' / ')}` : `${cpu.core_count || '?'} 核`,
            percent: cpu.percent || 0,
        },
        {
            label: '内存占用',
            value: resources.resource_ok ? `${memory.percent ?? 0}<small>%</small>` : '—',
            sub: memory.total_mb ? `${Math.round(memory.used_mb)} / ${Math.round(memory.total_mb)} MB` : '',
            percent: memory.percent || 0,
        },
        {
            label: '磁盘占用',
            value: disk.percent != null ? `${disk.percent}<small>%</small>` : '—',
            sub: disk.free_gb != null ? `剩余 ${disk.free_gb} GB` : '',
            percent: disk.percent || 0,
        },
        {
            label: '正在处理请求',
            value: `${traffic.active_requests ?? 0}`,
            sub: `累计 ${traffic.total_requests ?? 0} 次 · 错误 ${traffic.total_errors ?? 0}`,
            percent: Math.min(100, (traffic.active_requests || 0) * 10),
        },
        {
            label: 'WS 在线连接',
            value: `${connections.ws_active ?? 0}`,
            sub: `断连率 ${connections.ws_loss_rate ?? 0}%`,
            percent: Math.min(100, (connections.ws_active || 0) * 2),
        },
        {
            label: '服务运行时长',
            value: `<span style="font-size:1.05rem;">${formatUptime(traffic.uptime_seconds)}</span>`,
            sub: `进程数 ${resources.process_count ?? '—'}`,
            percent: 0,
        },
    ];

    $('monitorTiles').innerHTML = tiles.map((tile) => `
        <div class="monitor-panel monitor-tile">
            <span class="monitor-tile__label">${tile.label}</span>
            <span class="monitor-tile__value">${tile.value}</span>
            <span class="monitor-tile__sub">${escapeHtml(tile.sub)}</span>
            <span class="monitor-tile__bar"><i style="width:${Math.min(100, tile.percent)}%;background:${levelColor(tile.percent)}"></i></span>
        </div>
    `).join('');
}

/* ------------------------------------------------------------- trend chart */

function renderTrendChart(history) {
    const svg = $('trendChart');
    svg.innerHTML = '';
    const width = 760;
    const height = 220;
    const padding = { top: 12, right: 12, bottom: 22, left: 34 };
    const innerW = width - padding.left - padding.right;
    const innerH = height - padding.top - padding.bottom;

    for (const value of [0, 25, 50, 75, 100]) {
        const y = padding.top + innerH * (1 - value / 100);
        svg.appendChild(el('line', { x1: padding.left, y1: y, x2: width - padding.right, y2: y, stroke: COLORS.grid, 'stroke-width': 1 }));
        svg.appendChild(el('text', { x: padding.left - 6, y: y + 3, fill: COLORS.muted, 'font-size': 9, 'text-anchor': 'end' }, `${value}`));
    }

    if (!history.length) {
        svg.appendChild(el('text', { x: width / 2, y: height / 2, fill: COLORS.muted, 'font-size': 12, 'text-anchor': 'middle' }, '采样中，稍候片刻…'));
        return;
    }

    const step = history.length > 1 ? innerW / (history.length - 1) : 0;
    const pointX = (index) => padding.left + (history.length > 1 ? index * step : innerW / 2);
    const pointY = (value) => padding.top + innerH * (1 - Math.min(100, Math.max(0, value)) / 100);

    const buildPath = (key) => history
        .map((sample, index) => `${index === 0 ? 'M' : 'L'}${pointX(index).toFixed(1)},${pointY(sample[key] || 0).toFixed(1)}`)
        .join(' ');

    const cpuArea = `${buildPath('cpu_percent')} L${pointX(history.length - 1).toFixed(1)},${padding.top + innerH} L${pointX(0).toFixed(1)},${padding.top + innerH} Z`;
    svg.appendChild(el('path', { d: cpuArea, fill: 'rgba(56, 189, 248, 0.12)' }));
    svg.appendChild(el('path', { d: buildPath('cpu_percent'), fill: 'none', stroke: COLORS.accent, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
    svg.appendChild(el('path', { d: buildPath('memory_percent'), fill: 'none', stroke: COLORS.violet, 'stroke-width': 2, 'stroke-linejoin': 'round' }));

    const last = history[history.length - 1];
    svg.appendChild(el('circle', { cx: pointX(history.length - 1), cy: pointY(last.cpu_percent || 0), r: 3.5, fill: COLORS.accent }));
    svg.appendChild(el('circle', { cx: pointX(history.length - 1), cy: pointY(last.memory_percent || 0), r: 3.5, fill: COLORS.violet }));

    svg.appendChild(el('text', { x: padding.left, y: height - 6, fill: COLORS.muted, 'font-size': 9 }, formatTime(history[0].at)));
    svg.appendChild(el('text', { x: width - padding.right, y: height - 6, fill: COLORS.muted, 'font-size': 9, 'text-anchor': 'end' }, formatTime(last.at)));
}

/* --------------------------------------------------------- pressure chart */

function renderPressureChart(history) {
    const svg = $('pressureChart');
    svg.innerHTML = '';
    const width = 360;
    const height = 160;
    const padding = { top: 10, right: 8, bottom: 20, left: 8 };
    const innerH = height - padding.top - padding.bottom;
    const samples = history.slice(-40);

    if (!samples.length) {
        svg.appendChild(el('text', { x: width / 2, y: height / 2, fill: COLORS.muted, 'font-size': 12, 'text-anchor': 'middle' }, '采样中…'));
        return;
    }

    const peak = Math.max(1, ...samples.map((sample) => sample.requests_delta || 0));
    const slot = (width - padding.left - padding.right) / samples.length;
    const barW = Math.max(2, slot - 2);

    samples.forEach((sample, index) => {
        const requests = sample.requests_delta || 0;
        const errors = sample.errors_delta || 0;
        const x = padding.left + index * slot;
        const barH = Math.max(requests > 0 ? 2 : 0, (requests / peak) * innerH);
        svg.appendChild(el('rect', {
            x, y: padding.top + innerH - barH, width: barW, height: Math.max(barH, 0.5),
            rx: 1.5, fill: errors > 0 ? COLORS.bad : COLORS.accent, opacity: errors > 0 ? 0.95 : 0.8,
        }));
    });

    svg.appendChild(el('text', { x: padding.left, y: height - 5, fill: COLORS.muted, 'font-size': 9 }, `峰值 ${peak} 次/5s`));
    const recent = samples[samples.length - 1];
    svg.appendChild(el('text', {
        x: width - padding.right, y: height - 5, fill: COLORS.muted, 'font-size': 9, 'text-anchor': 'end',
    }, `最新 ${recent.requests_delta || 0} 次 · 并发 ${recent.active_requests || 0}`));
}

/* ------------------------------------------------------------------ donuts */

function renderDonut(svgId, listId, segments, centerLabel) {
    const svg = $(svgId);
    svg.innerHTML = '';
    const cx = 60;
    const cy = 60;
    const radius = 46;
    const circumference = 2 * Math.PI * radius;
    const total = segments.reduce((sum, segment) => sum + segment.value, 0);

    svg.appendChild(el('circle', { cx, cy, r: radius, fill: 'none', stroke: COLORS.grid, 'stroke-width': 13 }));

    let offset = 0;
    if (total > 0) {
        segments.forEach((segment) => {
            if (segment.value <= 0) return;
            const fraction = segment.value / total;
            svg.appendChild(el('circle', {
                cx, cy, r: radius, fill: 'none',
                stroke: segment.color, 'stroke-width': 13,
                'stroke-dasharray': `${(fraction * circumference).toFixed(2)} ${circumference.toFixed(2)}`,
                'stroke-dashoffset': (-offset * circumference).toFixed(2),
                transform: `rotate(-90 ${cx} ${cy})`,
            }));
            offset += fraction;
        });
    }

    svg.appendChild(el('text', { x: cx, y: cy - 2, fill: '#e7edf9', 'font-size': 17, 'font-weight': 800, 'text-anchor': 'middle' }, centerLabel.value));
    svg.appendChild(el('text', { x: cx, y: cy + 14, fill: COLORS.muted, 'font-size': 8.5, 'text-anchor': 'middle' }, centerLabel.caption));

    $(listId).innerHTML = segments.map((segment) => {
        const percent = total > 0 ? Math.round((segment.value / total) * 100) : 0;
        return `<span><i style="display:inline-block;width:8px;height:8px;border-radius:3px;background:${segment.color};margin-right:6px;"></i>${escapeHtml(segment.label)} <b>${escapeHtml(segment.display ?? String(segment.value))}</b>（${percent}%）</span>`;
    }).join('');
}

function renderStatusDonut(traffic) {
    const counts = traffic.status_counts || {};
    const buckets = { '2xx': 0, '3xx': 0, '4xx': 0, '5xx': 0 };
    Object.entries(counts).forEach(([code, count]) => {
        const bucket = `${String(code).charAt(0)}xx`;
        if (buckets[bucket] != null) buckets[bucket] += Number(count) || 0;
    });
    const total = Object.values(buckets).reduce((sum, value) => sum + value, 0);
    renderDonut(
        'statusDonut',
        'statusDonutList',
        Object.entries(buckets).map(([label, value]) => ({ label, value, color: STATUS_COLORS[label] })),
        { value: total >= 1000 ? `${(total / 1000).toFixed(1)}k` : String(total), caption: '总请求' },
    );
}

function renderMemoryDonut(resources) {
    const memory = resources.memory || {};
    const used = Math.max(0, (memory.used_mb || 0) - (memory.cached_mb || 0));
    const cached = memory.cached_mb || 0;
    const available = memory.available_mb || 0;
    renderDonut(
        'memoryDonut',
        'memoryDonutList',
        [
            { label: '已用', value: used, color: COLORS.violet, display: `${Math.round(used)}M` },
            { label: '缓存', value: cached, color: COLORS.accent, display: `${Math.round(cached)}M` },
            { label: '可用', value: available, color: COLORS.good, display: `${Math.round(available)}M` },
        ],
        { value: `${memory.percent ?? 0}%`, caption: '内存占用' },
    );
}

/* -------------------------------------------------------------- loss stats */

function renderLossStats(connections) {
    const stats = [
        { label: '累计连接', value: connections.ws_total ?? 0 },
        { label: '断开次数', value: connections.ws_disconnects ?? 0 },
        { label: '连接错误', value: connections.ws_errors ?? 0 },
        { label: '断连率', value: `${connections.ws_loss_rate ?? 0}%` },
    ];
    $('lossStats').innerHTML = stats.map((item) => `
        <div><span>${item.label}</span><b>${escapeHtml(String(item.value))}</b></div>
    `).join('');

    const errors = connections.recent_ws_errors || [];
    $('lossErrors').innerHTML = errors.length
        ? errors.slice().reverse().map((error) => `
            <div>${formatTime(error.at)} · 房间 ${escapeHtml(String(error.room_id ?? ''))} <code>${escapeHtml(error.message || '')}</code></div>
        `).join('')
        : '<div class="monitor-empty">暂无连接错误，链路稳定</div>';
}

/* -------------------------------------------------------------- route table */

function renderRoutes(traffic) {
    const routes = traffic.top_routes || [];
    const tbody = $('routesTable').querySelector('tbody');
    if (!routes.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="monitor-empty">暂无路由数据</td></tr>';
        return;
    }
    const peak = Math.max(1, ...routes.map((route) => route.count || 0));
    tbody.innerHTML = routes.map((route) => `
        <tr>
            <td class="route-cell route-bar" style="--route-ratio:${((route.count || 0) / peak).toFixed(3)}" title="${escapeHtml(route.route_path || '')}">
                ${escapeHtml(route.method || '')} ${escapeHtml(route.route_path || '')}
            </td>
            <td>${route.count ?? 0}</td>
            <td>${route.avg_duration_ms ?? 0}</td>
            <td style="color:${(route.p95_duration_ms || 0) > 1000 ? COLORS.warn : 'inherit'}">${route.p95_duration_ms ?? 0}</td>
            <td>${route.max_duration_ms ?? 0}</td>
            <td style="color:${(route.error_count || 0) > 0 ? COLORS.bad : 'inherit'}">${route.error_count ?? 0}</td>
        </tr>
    `).join('');
}

/* ------------------------------------------------------------- process tree */

function buildProcForest(processes) {
    const byPid = new Map(processes.map((proc) => [proc.pid, { ...proc, children: [] }]));
    const roots = [];
    byPid.forEach((node) => {
        const parent = byPid.get(node.ppid);
        if (parent && parent !== node) {
            parent.children.push(node);
        } else {
            roots.push(node);
        }
    });
    const sortNodes = (nodes) => {
        nodes.sort((a, b) => b.memory_mb - a.memory_mb || b.cpu_percent - a.cpu_percent);
        nodes.forEach((node) => sortNodes(node.children));
    };
    sortNodes(roots);
    return roots;
}

function procMatches(node, query) {
    if (!query) return true;
    const haystack = `${node.pid} ${node.name} ${node.cmdline}`.toLowerCase();
    if (haystack.includes(query)) return true;
    return node.children.some((child) => procMatches(child, query));
}

function renderProcRow(node, depth, query, rows) {
    if (!procMatches(node, query)) return;
    const hasChildren = node.children.length > 0;
    const collapsed = state.collapsedPids.has(node.pid) && !query;
    rows.push(`
        <div class="proc-row" data-pid="${node.pid}">
            <span class="proc-name" style="padding-left:${depth * 16}px" title="${escapeHtml(node.cmdline || node.name)}">
                ${hasChildren
                    ? `<button type="button" class="twisty" data-toggle-pid="${node.pid}" aria-label="展开/折叠子进程">${collapsed ? '▸' : '▾'}</button>`
                    : '<span class="twisty" aria-hidden="true"></span>'}
                <span class="proc-name__label">${escapeHtml(node.name || `pid ${node.pid}`)}</span>
                ${node.is_self ? '<span class="proc-self-badge">本服务</span>' : ''}
            </span>
            <span>${node.pid}</span>
            <span>${node.memory_mb} MB</span>
            <span>${node.cpu_percent}%</span>
            <span>${node.is_self
                ? '<button type="button" class="proc-kill" disabled title="禁止终止 Web 服务自身">受保护</button>'
                : `<button type="button" class="proc-kill" data-kill-pid="${node.pid}" data-kill-name="${escapeHtml(node.name || '')}">终止</button>`}
            </span>
        </div>
    `);
    if (!collapsed) {
        node.children.forEach((child) => renderProcRow(child, depth + 1, query, rows));
    }
}

function renderProcTree() {
    const container = $('procTree');
    const query = state.procFilter.trim().toLowerCase();
    const forest = buildProcForest(state.processes);
    const rows = [
        '<div class="proc-row proc-row--head"><span>进程</span><span>PID</span><span>内存</span><span>CPU</span><span>操作</span></div>',
    ];
    forest.forEach((node) => renderProcRow(node, 0, query, rows));
    container.innerHTML = rows.length > 1
        ? rows.join('')
        : `${rows[0]}<div class="monitor-empty">没有匹配的进程</div>`;
}

async function refreshProcesses() {
    try {
        const data = await apiGet('/api/manage/system/monitor/processes');
        state.processes = data.processes || [];
        state.selfPid = data.self_pid || 0;
        $('procMeta').textContent = data.resource_ok
            ? `共 ${data.total_count} 个进程 · 显示占用最高的 ${data.shown_count} 个 · ${formatTime(data.sampled_at)}`
            : (data.reason || '进程数据不可用');
        renderProcTree();
    } catch (error) {
        $('procMeta').textContent = `进程数据获取失败：${error.message}`;
    }
}

async function killProcess(pid, name, button) {
    const force = button.dataset.retryForce === '1';
    const label = force ? '强制终止（SIGKILL）' : '终止（SIGTERM）';
    if (!window.confirm(`确认${label}进程 ${pid}（${name}）？\n终止关键进程可能影响平台服务，请谨慎操作。`)) {
        return;
    }
    button.disabled = true;
    button.textContent = '处理中…';
    try {
        const data = await apiPost(`/api/manage/system/monitor/processes/${pid}/terminate`, { force });
        if (data.result?.alive_after) {
            button.disabled = false;
            button.textContent = '强制终止';
            button.dataset.retryForce = '1';
            window.showMessage?.(`进程 ${pid} 未响应终止信号，可再次点击强制终止。`, 'warning');
        } else {
            window.showMessage?.(`进程 ${pid}（${name}）已终止。`, 'success');
            await refreshProcesses();
        }
    } catch (error) {
        button.disabled = false;
        button.textContent = '终止';
        window.showMessage?.(error.message || '终止失败', 'error');
    }
}

/* ---------------------------------------------------------------- insight */

function renderInsightGauge(score) {
    const svg = $('insightGauge');
    svg.innerHTML = '';
    const cx = 60;
    const cy = 60;
    const radius = 48;
    const circumference = 2 * Math.PI * radius;
    const color = score >= 80 ? COLORS.good : score >= 60 ? COLORS.warn : COLORS.bad;
    svg.appendChild(el('circle', { cx, cy, r: radius, fill: 'none', stroke: COLORS.grid, 'stroke-width': 10 }));
    svg.appendChild(el('circle', {
        cx, cy, r: radius, fill: 'none', stroke: color, 'stroke-width': 10, 'stroke-linecap': 'round',
        'stroke-dasharray': `${((score / 100) * circumference).toFixed(2)} ${circumference.toFixed(2)}`,
        transform: `rotate(-90 ${cx} ${cy})`,
    }));
    svg.appendChild(el('text', { x: cx, y: cy + 2, fill: color, 'font-size': 26, 'font-weight': 800, 'text-anchor': 'middle' }, String(score)));
    svg.appendChild(el('text', { x: cx, y: cy + 20, fill: COLORS.muted, 'font-size': 9, 'text-anchor': 'middle' }, '健康评分'));
}

function renderInsight(insight) {
    const panel = $('monitorInsight');
    panel.classList.add('is-visible');
    $('monitorInsightAt').textContent = insight.generated_at ? `生成于 ${formatTime(insight.generated_at)}` : '';
    renderInsightGauge(insight.health_score || 0);
    $('insightSummary').textContent = insight.summary || 'AI 未返回总结。';

    const sections = [
        { title: '亮点', items: insight.highlights, color: COLORS.good },
        { title: '风险', items: insight.risks, color: COLORS.bad },
        { title: '建议', items: insight.suggestions, color: COLORS.accent },
    ].filter((section) => (section.items || []).length);

    $('insightLists').innerHTML = sections.length
        ? sections.map((section) => `
            <div>
                <h3 style="color:${section.color}">${section.title}</h3>
                <ul>${section.items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
            </div>
        `).join('')
        : '<div class="monitor-empty">AI 未给出分项分析</div>';
}

/* ---------------------------------------------------------------- refresh */

async function refreshSnapshot() {
    if (state.loading) return;
    state.loading = true;
    try {
        const data = await apiGet('/api/manage/system/monitor/snapshot');
        const snapshot = data.snapshot || {};
        const resources = snapshot.resources || {};

        renderTiles(snapshot);
        renderTrendChart(snapshot.history || []);
        renderPressureChart(snapshot.history || []);
        renderStatusDonut(snapshot.traffic || {});
        renderMemoryDonut(resources);
        renderLossStats(snapshot.connections || {});
        renderRoutes(snapshot.traffic || {});

        $('monitorMeta').textContent = `数据时间 ${formatTime(snapshot.generated_at)} · 服务启动于 ${formatTime(snapshot.traffic?.started_at)}`;
        $('monitorBoot').textContent = resources.boot_time
            ? `服务器开机时间 ${new Date(resources.boot_time).toLocaleString('zh-CN', { hour12: false })}`
            : '';
    } catch (error) {
        $('monitorMeta').textContent = `监控数据获取失败：${error.message}`;
    } finally {
        state.loading = false;
    }
}

function scheduleAutoRefresh() {
    if (state.timer) window.clearInterval(state.timer);
    state.timer = null;
    if (state.autoRefresh) {
        state.timer = window.setInterval(() => {
            if (!document.hidden) refreshSnapshot();
        }, REFRESH_INTERVAL_MS);
    }
}

/* ------------------------------------------------------------------- init */

function bindEvents() {
    $('monitorAutoBtn').addEventListener('click', (event) => {
        state.autoRefresh = !state.autoRefresh;
        event.currentTarget.textContent = `自动刷新：${state.autoRefresh ? '开' : '关'}`;
        event.currentTarget.classList.toggle('is-on', state.autoRefresh);
        scheduleAutoRefresh();
    });

    $('monitorRefreshBtn').addEventListener('click', () => {
        refreshSnapshot();
        refreshProcesses();
    });

    $('monitorMemBtn').addEventListener('click', async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        button.textContent = '优化中…';
        try {
            const data = await apiPost('/api/manage/system/monitor/memory/optimize');
            const result = data.result || {};
            window.showMessage?.(
                `内存优化完成：回收 ${result.collected_objects ?? 0} 个对象，释放 ${result.freed_mb ?? 0} MB（RSS ${result.rss_before_mb ?? '?'} → ${result.rss_after_mb ?? '?'} MB）`,
                'success',
            );
            refreshSnapshot();
        } catch (error) {
            window.showMessage?.(error.message || '内存优化失败', 'error');
        } finally {
            button.disabled = false;
            button.textContent = '内存一键优化';
        }
    });

    $('monitorAiBtn').addEventListener('click', async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        button.textContent = 'AI 分析中…';
        try {
            const data = await apiPost('/api/manage/system/monitor/ai-insight');
            renderInsight(data.insight || {});
        } catch (error) {
            window.showMessage?.(error.message || 'AI 解读失败', 'error');
        } finally {
            button.disabled = false;
            button.textContent = 'AI 解读大屏';
        }
    });

    $('procRefreshBtn').addEventListener('click', refreshProcesses);

    $('procSearch').addEventListener('input', (event) => {
        state.procFilter = event.target.value || '';
        renderProcTree();
    });

    $('procTree').addEventListener('click', (event) => {
        const toggle = event.target.closest('[data-toggle-pid]');
        if (toggle) {
            const pid = Number(toggle.dataset.togglePid);
            if (state.collapsedPids.has(pid)) {
                state.collapsedPids.delete(pid);
            } else {
                state.collapsedPids.add(pid);
            }
            renderProcTree();
            return;
        }
        const killButton = event.target.closest('[data-kill-pid]');
        if (killButton && !killButton.disabled) {
            killProcess(Number(killButton.dataset.killPid), killButton.dataset.killName || '', killButton);
        }
    });

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && state.autoRefresh) refreshSnapshot();
    });
}

bindEvents();
refreshSnapshot();
refreshProcesses();
scheduleAutoRefresh();

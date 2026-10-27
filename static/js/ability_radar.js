/**
 * 六边形能力雷达的原生 JS 渲染器 —— 与 templates/partials/ability_radar.html 输出同一
 * DOM/类名（样式 static/css/lq/components/ability-radar.css），数据为
 * ability_radar_service.build_ability_radar(...) 的载荷（几何点位由服务端算好）。
 * 用于异步浮层（个人信息面板）；服务端页面继续用 Jinja 宏。只用 textContent / setAttribute，
 * 不拼 HTML。
 */
const SVG_NS = 'http://www.w3.org/2000/svg';

const isNum = (value) => typeof value === 'number' && Number.isFinite(value);
const show = (value) => (value === null || value === undefined ? null : value);

function svg(doc, tag, attrs = {}, text = null) {
    const node = doc.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value !== null && value !== undefined) node.setAttribute(key, String(value));
    }
    if (text !== null) node.textContent = String(text);
    return node;
}

function html(doc, tag, className = '', text = null) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== null) node.textContent = String(text);
    return node;
}

function titled(doc, node, title) {
    node.append(svg(doc, 'title', {}, title));
    return node;
}

function ariaLabel(radar, selfLabel) {
    const parts = (radar.axes || []).map((axis) => {
        let text = `${axis.label} ${show(axis.score) ?? '暂无'}`;
        if (show(axis.average) !== null) text += `（${radar.peer_label} ${axis.average}）`;
        return text;
    });
    return `${selfLabel}能力雷达：${parts.join('，')}`;
}

function figure(doc, radar, selfLabel) {
    const center = Number(radar.center) || 120;
    const root = svg(doc, 'svg', { class: 'lq-radar__svg', viewBox: '-58 -14 356 268', role: 'img', 'aria-label': ariaLabel(radar, selfLabel) });
    for (const ring of radar.rings || []) {
        root.append(svg(doc, 'polygon', { class: `lq-radar__ring${ring.ratio === 100 ? ' is-outer' : ''}`, points: ring.points }));
    }
    for (const spoke of radar.spokes || []) {
        root.append(svg(doc, 'line', { class: 'lq-radar__spoke', x1: center, y1: center, x2: spoke.x, y2: spoke.y }));
    }
    for (const ring of (radar.rings || []).slice(0, 3)) {
        root.append(svg(doc, 'text', { class: 'lq-radar__tick', x: center + 3, y: center - (80 * ring.ratio) / 100 + 3 }, ring.ratio));
    }
    if (radar.has_average) root.append(titled(doc, svg(doc, 'polygon', { class: 'lq-radar__average', points: radar.average_points }), radar.peer_label));
    root.append(titled(doc, svg(doc, 'polygon', { class: 'lq-radar__self', points: radar.points }), selfLabel));
    for (const axis of radar.axes || []) {
        if (show(axis.score) !== null) {
            root.append(titled(doc, svg(doc, 'circle', { class: 'lq-radar__vertex', cx: axis.vertex_x, cy: axis.vertex_y, r: '3.4' }), `${axis.label} ${axis.score}`));
        }
        const label = svg(doc, 'text', { class: 'lq-radar__label', x: axis.label_x, y: axis.label_y, 'text-anchor': axis.anchor });
        label.append(svg(doc, 'tspan', { x: axis.label_x }, axis.label));
        const value = svg(doc, 'tspan', { class: 'lq-radar__label-value', x: axis.label_x, dy: '15' }, show(axis.score) ?? '—');
        if (show(axis.average) !== null) value.append(svg(doc, 'tspan', { class: 'lq-radar__label-average' }, ` / ${axis.average}`));
        label.append(value);
        root.append(label);
    }
    const legend = html(doc, 'figcaption', 'lq-radar__legend');
    legend.append(html(doc, 'span', 'lq-radar__key is-self', selfLabel));
    if (radar.has_average) legend.append(html(doc, 'span', 'lq-radar__key is-average', `${radar.peer_label}${radar.peer_count ? ` · ${radar.peer_count} 人` : ''}`));
    if (Number(radar.course_count) > 1) legend.append(html(doc, 'span', 'lq-radar__key is-scope', `综合 ${radar.course_count} 门课堂`));
    const fig = html(doc, 'figure', 'lq-radar__figure');
    fig.append(root, legend);
    return fig;
}

function insight(doc, radar) {
    const { strength, focus } = radar;
    if (!strength && !focus) return null;
    const node = html(doc, 'p', 'lq-radar__insight');
    const line = (prefix, labelText, suffix) => {
        const span = html(doc, 'span');
        span.append(prefix, html(doc, 'strong', '', labelText), suffix);
        return span;
    };
    if (strength) {
        node.append(isNum(strength.delta) && strength.delta > 0
            ? line('优势 ', strength.label, `，高于${radar.peer_label} ${strength.delta}`)
            : line('相对强项 ', strength.label, ''));
    }
    if (focus) {
        node.append(isNum(focus.delta) && focus.delta < 0
            ? line('待加强 ', focus.label, `，低于${radar.peer_label} ${-focus.delta}`)
            : line('可再提升 ', focus.label, ''));
    }
    return node;
}

function axisList(doc, radar) {
    const list = html(doc, 'ul', 'lq-radar__axes');
    for (const axis of radar.axes || []) {
        const score = show(axis.score);
        const item = html(doc, 'li', `lq-radar__axis${score === null ? ' is-empty' : ''}`);
        item.append(html(doc, 'span', 'lq-radar__axis-name', axis.label), html(doc, 'strong', 'lq-radar__axis-value', score ?? '暂无'));
        const delta = html(doc, 'span', 'lq-radar__delta');
        if (isNum(axis.delta)) {
            delta.dataset.tone = axis.delta >= 5 ? 'success' : (axis.delta <= -5 ? 'warning' : 'neutral');
            delta.textContent = `${axis.delta > 0 ? '+' : ''}${axis.delta}`;
        } else {
            delta.dataset.tone = 'neutral';
            delta.setAttribute('aria-hidden', 'true');
        }
        const bar = html(doc, 'span', 'lq-radar__bar');
        bar.setAttribute('aria-hidden', 'true');
        const self = html(doc, 'i');
        self.style.setProperty('--value', `${Number(score) || 0}%`);
        bar.append(self);
        if (show(axis.average) !== null) {
            const peer = html(doc, 'b');
            peer.style.setProperty('--value', `${Number(axis.average) || 0}%`);
            bar.append(peer);
        }
        item.append(delta, bar);
        list.append(item);
    }
    return list;
}

/**
 * @param {Record<string, any>|null|undefined} radar build_ability_radar payload
 * @param {{selfLabel?: string, doc?: Document}} [options]
 * @returns {HTMLElement}
 */
export function renderAbilityRadar(radar, { selfLabel = '本人', doc = document } = {}) {
    if (!radar || !radar.available || !Array.isArray(radar.axes)) {
        return html(doc, 'p', 'lq-radar__empty', '还没有可用的学习数据：完成一次研读、提交或互动后，这里会生成能力雷达。');
    }
    const root = html(doc, 'div', 'lq-radar');
    root.dataset.lqComponent = 'radar';
    const inner = html(doc, 'div', 'lq-radar__inner');
    const detail = html(doc, 'div', 'lq-radar__detail');
    const lead = insight(doc, radar);
    if (lead) detail.append(lead);
    detail.append(axisList(doc, radar));
    inner.append(figure(doc, radar, selfLabel), detail);
    root.append(inner);
    return root;
}

// ls_date_picker.js — 全站统一日期/时间选择器
// 自动接管页面上所有原生 input[type=date|datetime-local|time]（含动态插入的节点），
// 原生 input 保留在 DOM 中作为取值载体（value 格式不变），现有读写 .value 的代码零改动。
// 选择器 UI：中文显示、周一起始、年/月/日三级导航、时间滚动列、移动端底部抽屉。
// 可选配对（日期范围）：在两个 input 上设置
//   data-dp-pair="<选择器>"（先在最近的 form 内解析，再退回 document）
//   data-dp-role="start" | "end"（end 一侧会以 start 的值作为最小可选日期）
// 退出机制：input 或祖先带有 data-ls-native 属性时不接管。

(() => {
    'use strict';

    const ENHANCED_FLAG = 'lsDpEnhanced';
    const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];
    const DUE_HINT_RE = /due|deadline|end|expire|late|until|close/i;
    const MOBILE_QUERY = window.matchMedia('(max-width: 640px)');
    const REDUCE_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');

    const ICON_CALENDAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';
    const ICON_CLOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';

    const pad2 = (n) => String(n).padStart(2, '0');

    function parseDateValue(kind, raw) {
        if (!raw) return null;
        if (kind === 'time') {
            const m = /^(\d{1,2}):(\d{2})/.exec(raw);
            if (!m) return null;
            return { h: Math.min(23, +m[1]), min: Math.min(59, +m[2]) };
        }
        const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(raw);
        if (!m) return null;
        return {
            y: +m[1], mo: +m[2] - 1, d: +m[3],
            h: m[4] !== undefined ? +m[4] : null,
            min: m[5] !== undefined ? +m[5] : null,
        };
    }

    function formatDisplay(kind, raw) {
        const v = parseDateValue(kind, raw);
        if (!v) return '';
        if (kind === 'time') return `${pad2(v.h)}:${pad2(v.min)}`;
        const dateText = `${v.y}年${v.mo + 1}月${v.d}日`;
        if (kind === 'datetime' && v.h !== null) return `${dateText} ${pad2(v.h)}:${pad2(v.min)}`;
        return dateText;
    }

    function placeholderFor(kind) {
        if (kind === 'time') return '选择时间';
        if (kind === 'datetime') return '选择日期时间';
        return '选择日期';
    }

    function kindOf(input) {
        const t = (input.getAttribute('type') || '').toLowerCase();
        if (t === 'date') return 'date';
        if (t === 'datetime-local') return 'datetime';
        if (t === 'time') return 'time';
        return null;
    }

    function dispatchValueEvents(input) {
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function daysInMonth(y, mo) { return new Date(y, mo + 1, 0).getDate(); }

    function dateKey(y, mo, d) { return y * 10000 + (mo + 1) * 100 + d; }

    function resolvePair(input) {
        const sel = input.dataset.dpPair;
        if (!sel) return null;
        try {
            const scope = input.closest('form');
            const other = (scope && scope.querySelector(sel)) || document.querySelector(sel);
            return other && other !== input ? other : null;
        } catch { return null; }
    }

    // ---------------------------------------------------------------- popup —

    let activePopup = null; // { root, backdrop, input, kind, state, teardown }

    function closePopup(commitAnimation) {
        if (!activePopup) return;
        const { root, backdrop, teardown } = activePopup;
        activePopup = null;
        teardown();
        const remove = () => { root.remove(); if (backdrop) backdrop.remove(); };
        if (REDUCE_MOTION.matches) { remove(); return; }
        root.classList.add('is-closing');
        if (backdrop) backdrop.classList.add('is-closing');
        setTimeout(remove, commitAnimation ? 200 : 150);
    }

    function defaultTimeFor(input) {
        const hint = `${input.id || ''} ${input.name || ''}`;
        if (DUE_HINT_RE.test(hint)) return { h: 23, min: 59 };
        const now = new Date();
        return { h: now.getHours(), min: now.getMinutes() };
    }

    function minuteStepFor(input) {
        const step = parseInt(input.getAttribute('step') || '', 10);
        if (Number.isFinite(step) && step >= 120 && step % 60 === 0) return step / 60;
        return 1;
    }

    function openPopup(input, kind) {
        if (activePopup) {
            const same = activePopup.input === input;
            closePopup(false);
            if (same) return;
        }
        const today = new Date();
        const cur = parseDateValue(kind, input.value);
        const defTime = defaultTimeFor(input);
        const state = {
            mode: 'days',
            viewY: cur && cur.y !== undefined ? cur.y : today.getFullYear(),
            viewMo: cur && cur.mo !== undefined ? cur.mo : today.getMonth(),
            selY: cur && cur.y !== undefined ? cur.y : null,
            selMo: cur && cur.mo !== undefined ? cur.mo : null,
            selD: cur ? cur.d : null,
            selH: cur && cur.h !== null && cur.h !== undefined ? cur.h : defTime.h,
            selMin: cur && cur.min !== null && cur.min !== undefined ? cur.min : defTime.min,
        };
        if (kind === 'time' && cur) { state.selH = cur.h; state.selMin = cur.min; }

        // 范围/边界约束
        const minAttr = parseDateValue('date', input.getAttribute('min') || '');
        const maxAttr = parseDateValue('date', input.getAttribute('max') || '');
        const pairInput = resolvePair(input);
        const role = input.dataset.dpRole || '';
        const pairVal = pairInput ? parseDateValue(kindOf(pairInput) || kind, pairInput.value) : null;
        let minKey = minAttr && minAttr.y ? dateKey(minAttr.y, minAttr.mo, minAttr.d) : null;
        let maxKey = maxAttr && maxAttr.y ? dateKey(maxAttr.y, maxAttr.mo, maxAttr.d) : null;
        if (role === 'end' && pairVal && pairVal.y) {
            const pk = dateKey(pairVal.y, pairVal.mo, pairVal.d);
            if (minKey === null || pk > minKey) minKey = pk;
        }
        const rangeOther = pairVal && pairVal.y ? dateKey(pairVal.y, pairVal.mo, pairVal.d) : null;

        const mobile = MOBILE_QUERY.matches;
        const root = document.createElement('div');
        root.className = `ls-dp-pop ls-dp-pop--${kind}${mobile ? ' is-sheet' : ''}`;
        root.setAttribute('role', 'dialog');
        root.setAttribute('aria-label', placeholderFor(kind));
        let backdrop = null;
        if (mobile) {
            backdrop = document.createElement('div');
            backdrop.className = 'ls-dp-backdrop';
            document.body.appendChild(backdrop);
        }
        document.body.appendChild(root);

        const commitValue = () => {
            if (kind === 'time') {
                input.value = `${pad2(state.selH)}:${pad2(state.selMin)}`;
            } else if (state.selY !== null && state.selD !== null) {
                const dateText = `${state.selY}-${pad2(state.selMo + 1)}-${pad2(state.selD)}`;
                input.value = kind === 'datetime'
                    ? `${dateText}T${pad2(state.selH)}:${pad2(state.selMin)}`
                    : dateText;
            } else {
                return;
            }
            dispatchValueEvents(input);
        };

        const isDayDisabled = (y, mo, d) => {
            const k = dateKey(y, mo, d);
            if (minKey !== null && k < minKey) return true;
            if (maxKey !== null && k > maxKey) return true;
            return false;
        };

        const render = () => {
            root.innerHTML = '';
            if (mobile) {
                const grip = document.createElement('div');
                grip.className = 'ls-dp-grip';
                root.appendChild(grip);
            }
            if (kind !== 'time') renderCalendar();
            if (kind !== 'date') renderTime();
            renderFooter();
            if (!mobile) position();
        };

        const renderCalendar = () => {
            const head = document.createElement('div');
            head.className = 'ls-dp-head';
            const mkNav = (cls, label, delta) => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = `ls-dp-nav ${cls}`;
                b.setAttribute('aria-label', label);
                b.innerHTML = cls === 'prev'
                    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>'
                    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
                b.addEventListener('click', () => shiftView(delta));
                return b;
            };
            const title = document.createElement('button');
            title.type = 'button';
            title.className = 'ls-dp-title';
            if (state.mode === 'days') {
                title.textContent = `${state.viewY}年${state.viewMo + 1}月`;
            } else if (state.mode === 'months') {
                title.textContent = `${state.viewY}年`;
            } else {
                const base = Math.floor(state.viewY / 12) * 12;
                title.textContent = `${base} – ${base + 11}`;
            }
            title.addEventListener('click', () => {
                state.mode = state.mode === 'days' ? 'months' : 'years';
                render();
            });
            head.appendChild(mkNav('prev', '上一页', -1));
            head.appendChild(title);
            head.appendChild(mkNav('next', '下一页', 1));
            root.appendChild(head);

            const body = document.createElement('div');
            body.className = 'ls-dp-body';
            if (state.mode === 'days') body.appendChild(renderDays());
            else if (state.mode === 'months') body.appendChild(renderMonths());
            else body.appendChild(renderYears());
            root.appendChild(body);
        };

        let slideDir = 0;
        const shiftView = (delta) => {
            if (state.mode === 'days') {
                const mo = state.viewMo + delta;
                state.viewY += Math.floor(mo / 12);
                state.viewMo = ((mo % 12) + 12) % 12;
            } else if (state.mode === 'months') {
                state.viewY += delta;
            } else {
                state.viewY += delta * 12;
            }
            slideDir = delta;
            render();
        };

        const animClass = () => {
            if (REDUCE_MOTION.matches || !slideDir) return '';
            const cls = slideDir > 0 ? ' is-slide-left' : ' is-slide-right';
            slideDir = 0;
            return cls;
        };

        const renderDays = () => {
            const wrap = document.createElement('div');
            wrap.className = `ls-dp-days${animClass()}`;
            const dow = document.createElement('div');
            dow.className = 'ls-dp-dow';
            WEEKDAYS.forEach((w, i) => {
                const s = document.createElement('span');
                s.textContent = w;
                if (i >= 5) s.classList.add('is-weekend');
                dow.appendChild(s);
            });
            wrap.appendChild(dow);
            const grid = document.createElement('div');
            grid.className = 'ls-dp-grid';
            const first = new Date(state.viewY, state.viewMo, 1);
            const lead = (first.getDay() + 6) % 7; // 周一起始
            const dim = daysInMonth(state.viewY, state.viewMo);
            const prevDim = daysInMonth(state.viewY, state.viewMo - 1);
            const todayK = dateKey(today.getFullYear(), today.getMonth(), today.getDate());
            const selK = state.selY !== null && state.selD !== null ? dateKey(state.selY, state.selMo, state.selD) : null;
            const total = Math.ceil((lead + dim) / 7) * 7;
            for (let i = 0; i < total; i++) {
                let y = state.viewY, mo = state.viewMo, d = i - lead + 1, outside = false;
                if (i < lead) {
                    d = prevDim - lead + 1 + i; outside = true;
                    mo -= 1; if (mo < 0) { mo = 11; y -= 1; }
                } else if (d > dim) {
                    d -= dim; outside = true;
                    mo += 1; if (mo > 11) { mo = 0; y += 1; }
                }
                const k = dateKey(y, mo, d);
                const cell = document.createElement('button');
                cell.type = 'button';
                cell.className = 'ls-dp-day';
                cell.textContent = String(d);
                if (outside) cell.classList.add('is-outside');
                if (k === todayK) cell.classList.add('is-today');
                if (selK !== null && k === selK) cell.classList.add('is-selected');
                if (rangeOther !== null && selK !== null) {
                    const lo = Math.min(rangeOther, selK), hi = Math.max(rangeOther, selK);
                    if (k > lo && k < hi) cell.classList.add('is-in-range');
                    if (k === rangeOther) cell.classList.add('is-range-edge');
                }
                if (isDayDisabled(y, mo, d)) {
                    cell.disabled = true;
                } else {
                    cell.addEventListener('click', () => {
                        state.selY = y; state.selMo = mo; state.selD = d;
                        state.viewY = y; state.viewMo = mo;
                        commitValue();
                        if (kind === 'date') {
                            cell.classList.add('is-selected');
                            setTimeout(() => closePopup(true), REDUCE_MOTION.matches ? 0 : 140);
                        } else {
                            render();
                        }
                    });
                }
                grid.appendChild(cell);
            }
            wrap.appendChild(grid);
            return wrap;
        };

        const renderMonths = () => {
            const grid = document.createElement('div');
            grid.className = `ls-dp-grid ls-dp-grid--months${animClass()}`;
            for (let mo = 0; mo < 12; mo++) {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'ls-dp-cell';
                b.textContent = `${mo + 1}月`;
                if (mo === state.viewMo) b.classList.add('is-selected');
                if (state.viewY === today.getFullYear() && mo === today.getMonth()) b.classList.add('is-today');
                b.addEventListener('click', () => {
                    state.viewMo = mo;
                    state.mode = 'days';
                    render();
                });
                grid.appendChild(b);
            }
            return grid;
        };

        const renderYears = () => {
            const grid = document.createElement('div');
            grid.className = `ls-dp-grid ls-dp-grid--months${animClass()}`;
            const base = Math.floor(state.viewY / 12) * 12;
            for (let y = base; y < base + 12; y++) {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'ls-dp-cell';
                b.textContent = String(y);
                if (y === state.viewY) b.classList.add('is-selected');
                if (y === today.getFullYear()) b.classList.add('is-today');
                b.addEventListener('click', () => {
                    state.viewY = y;
                    state.mode = 'months';
                    render();
                });
                grid.appendChild(b);
            }
            return grid;
        };

        const renderTime = () => {
            const minStep = minuteStepFor(input);
            const wrap = document.createElement('div');
            wrap.className = 'ls-dp-time';
            const label = document.createElement('div');
            label.className = 'ls-dp-time-label';
            label.innerHTML = `${ICON_CLOCK}<span>时间</span><strong>${pad2(state.selH)}:${pad2(state.selMin)}</strong>`;
            wrap.appendChild(label);
            const cols = document.createElement('div');
            cols.className = 'ls-dp-time-cols';
            const mkCol = (count, step, selected, unit, onPick) => {
                const col = document.createElement('div');
                col.className = 'ls-dp-time-col';
                col.setAttribute('role', 'listbox');
                col.setAttribute('aria-label', unit);
                for (let i = 0; i < count; i += step) {
                    const b = document.createElement('button');
                    b.type = 'button';
                    b.className = 'ls-dp-time-item';
                    b.textContent = pad2(i);
                    if (i === selected) b.classList.add('is-selected');
                    b.addEventListener('click', () => {
                        onPick(i);
                        col.querySelectorAll('.is-selected').forEach((el) => el.classList.remove('is-selected'));
                        b.classList.add('is-selected');
                        b.scrollIntoView({ block: 'center', behavior: REDUCE_MOTION.matches ? 'auto' : 'smooth' });
                        label.querySelector('strong').textContent = `${pad2(state.selH)}:${pad2(state.selMin)}`;
                        if (kind === 'time' || state.selD !== null) commitValue();
                    });
                    col.appendChild(b);
                }
                return col;
            };
            cols.appendChild(mkCol(24, 1, state.selH, '小时', (v) => { state.selH = v; }));
            cols.appendChild(mkCol(60, minStep, state.selMin, '分钟', (v) => { state.selMin = v; }));
            wrap.appendChild(cols);
            root.appendChild(wrap);
            requestAnimationFrame(() => {
                cols.querySelectorAll('.ls-dp-time-col .is-selected').forEach((el) => {
                    const colEl = el.parentElement;
                    colEl.scrollTop = el.offsetTop - colEl.clientHeight / 2 + el.offsetHeight / 2;
                });
            });
        };

        const renderFooter = () => {
            const foot = document.createElement('div');
            foot.className = 'ls-dp-foot';
            const mkBtn = (text, cls, fn) => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = `ls-dp-btn ${cls}`;
                b.textContent = text;
                b.addEventListener('click', fn);
                return b;
            };
            if (!input.required) {
                foot.appendChild(mkBtn('清除', 'is-ghost', () => {
                    input.value = '';
                    dispatchValueEvents(input);
                    closePopup(false);
                }));
            }
            const spacer = document.createElement('span');
            spacer.className = 'ls-dp-foot-spacer';
            foot.appendChild(spacer);
            foot.appendChild(mkBtn(kind === 'date' ? '今天' : '此刻', 'is-ghost', () => {
                const now = new Date();
                state.selY = now.getFullYear(); state.selMo = now.getMonth(); state.selD = now.getDate();
                state.viewY = state.selY; state.viewMo = state.selMo;
                if (kind !== 'date') { state.selH = now.getHours(); state.selMin = now.getMinutes(); }
                commitValue();
                closePopup(true);
            }));
            if (kind !== 'date') {
                foot.appendChild(mkBtn('确定', 'is-primary', () => {
                    if (kind === 'datetime' && state.selD === null) {
                        // 未点选日期时默认今天
                        const now = new Date();
                        state.selY = now.getFullYear(); state.selMo = now.getMonth(); state.selD = now.getDate();
                    }
                    commitValue();
                    closePopup(true);
                }));
            }
            root.appendChild(foot);
        };

        const position = () => {
            const anchor = input.closest('.ls-dp') || input;
            const r = anchor.getBoundingClientRect();
            const pw = root.offsetWidth, ph = root.offsetHeight;
            const vw = window.innerWidth, vh = window.innerHeight;
            const left = Math.min(Math.max(8, r.left), vw - pw - 8);
            let top = r.bottom + 6;
            let above = false;
            if (top + ph > vh - 8 && r.top - ph - 6 > 8) { top = r.top - ph - 6; above = true; }
            top = Math.min(Math.max(8, top), Math.max(8, vh - ph - 8));
            root.style.left = `${Math.round(left)}px`;
            root.style.top = `${Math.round(top)}px`;
            root.classList.toggle('is-above', above);
        };

        // 事件与清理
        const onPointerDown = (e) => {
            if (root.contains(e.target)) return;
            const anchor = input.closest('.ls-dp');
            if (anchor && anchor.contains(e.target)) return;
            closePopup(false);
        };
        const onKeyDown = (e) => {
            if (e.key === 'Escape') { e.stopPropagation(); closePopup(false); }
        };
        const onReposition = () => { if (!mobile) position(); };
        document.addEventListener('pointerdown', onPointerDown, true);
        document.addEventListener('keydown', onKeyDown, true);
        window.addEventListener('resize', onReposition);
        window.addEventListener('scroll', onReposition, true);
        if (backdrop) backdrop.addEventListener('pointerdown', () => closePopup(false));
        // 屏蔽冒泡，避免误触页面级"点击外部关闭弹窗"处理器
        root.addEventListener('click', (e) => e.stopPropagation());

        const teardown = () => {
            document.removeEventListener('pointerdown', onPointerDown, true);
            document.removeEventListener('keydown', onKeyDown, true);
            window.removeEventListener('resize', onReposition);
            window.removeEventListener('scroll', onReposition, true);
        };

        activePopup = { root, backdrop, input, kind, state, teardown };
        render();
        if (!mobile) position();
        requestAnimationFrame(() => root.classList.add('is-open'));
    }

    // ------------------------------------------------------------- enhance —

    const nativeValueDesc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');

    function syncDisplay(input) {
        const wrap = input.closest('.ls-dp');
        if (!wrap) return;
        const kind = kindOf(input);
        const textEl = wrap.querySelector('.ls-dp-text');
        const display = wrap.querySelector('.ls-dp-display');
        if (!textEl || !display) return;
        const text = formatDisplay(kind, input.value);
        textEl.textContent = text || placeholderFor(kind);
        wrap.classList.toggle('has-value', !!text);
        display.disabled = input.disabled;
        wrap.classList.toggle('is-disabled', input.disabled);
    }

    function enhance(input) {
        if (input.dataset[ENHANCED_FLAG]) return;
        if (input.closest('[data-ls-native]')) return;
        const kind = kindOf(input);
        if (!kind) return;
        input.dataset[ENHANCED_FLAG] = '1';

        const wrap = document.createElement('span');
        wrap.className = `ls-dp ls-dp--${kind}`;
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        input.classList.add('ls-dp-native');
        input.setAttribute('tabindex', '-1');

        const display = document.createElement('button');
        display.type = 'button';
        display.className = 'ls-dp-display';
        display.innerHTML = `<span class="ls-dp-icon">${kind === 'time' ? ICON_CLOCK : ICON_CALENDAR}</span><span class="ls-dp-text"></span>`;
        wrap.appendChild(display);

        display.addEventListener('click', (e) => {
            e.preventDefault();
            if (input.disabled) return;
            openPopup(input, kind);
        });
        // 浏览器校验（required）聚焦到隐藏 input 时转为打开面板
        input.addEventListener('focus', () => {
            if (!input.disabled && (!activePopup || activePopup.input !== input)) openPopup(input, kind);
        });
        input.addEventListener('change', () => syncDisplay(input));

        // 拦截脚本对 .value 的直接赋值，保持显示同步
        if (nativeValueDesc) {
            Object.defineProperty(input, 'value', {
                configurable: true,
                get() { return nativeValueDesc.get.call(this); },
                set(v) {
                    nativeValueDesc.set.call(this, v);
                    syncDisplay(this);
                },
            });
        }

        syncDisplay(input);
    }

    function scan(rootNode) {
        const sel = 'input[type="date"], input[type="datetime-local"], input[type="time"]';
        if (rootNode instanceof Element && rootNode.matches(sel)) enhance(rootNode);
        if (rootNode.querySelectorAll) rootNode.querySelectorAll(sel).forEach(enhance);
    }

    function init() {
        scan(document);
        const observer = new MutationObserver((mutations) => {
            for (const mut of mutations) {
                if (mut.type === 'attributes') {
                    const t = mut.target;
                    if (t instanceof HTMLInputElement && t.dataset[ENHANCED_FLAG]) syncDisplay(t);
                    continue;
                }
                mut.addedNodes.forEach((node) => {
                    if (node instanceof Element) scan(node);
                });
            }
        });
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['disabled'],
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.LsDatePicker = { enhance, close: () => closePopup(false) };
})();

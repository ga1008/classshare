// Shared navigation primitives for the classroom workspace. Browsing never selects.
export function sessionMaterialScope(session) {
    if (!session || session.entry_type === 'academic_exam' || session.is_academic_exam) return null;
    if (session.is_home_entry || session.entry_type === 'home') return 0;
    return Number(session.id) > 0 ? Number(session.id) : null;
}

export function materialOpenUrl(raw, classOfferingId, sessionId, origin = window.location.origin) {
    try {
        const url = new URL(String(raw || ''), origin);
        if (!raw || url.origin !== origin || !['http:', 'https:'].includes(url.protocol)) return '';
        url.searchParams.set('class_offering_id', String(classOfferingId));
        if (Number(sessionId) > 0) url.searchParams.set('session_id', String(sessionId));
        else url.searchParams.delete('session_id');
        url.searchParams.set('classroom_reader_tab', '1');
        return url.pathname + url.search + url.hash;
    } catch { return ''; }
}

export function materialEntryDecision(entries, classOfferingId, sessionId, origin) {
    if (!Array.isArray(entries) || entries.some(entry => !entry || typeof entry !== 'object')) return { kind: 'unavailable', materials: [] };
    const materials = entries;
    if (!materials.length) return { kind: 'empty', materials };
    if (materials.length > 1) return { kind: 'list', materials };
    const url = materialOpenUrl(materials[0].open_url, classOfferingId, sessionId, origin);
    return url ? { kind: 'reader', url, materials } : { kind: 'unavailable', materials };
}

export function bindClassroomLessonRail({ rail, buttons, previous, next, current, sessions, select, activate }) {
    const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const scroll = left => rail.scrollBy({ left, behavior: reduced() ? 'auto' : 'smooth' });
    const reveal = button => {
        if (!button) return;
        const box = button.getBoundingClientRect(), viewport = rail.getBoundingClientRect();
        scroll(box.left - viewport.left - (rail.clientWidth - box.width) / 2);
    };
    const updateEdges = () => {
        if (previous) previous.disabled = rail.scrollLeft <= 1;
        if (next) next.disabled = rail.scrollLeft >= rail.scrollWidth - rail.clientWidth - 1;
    };
    previous?.addEventListener('click', () => scroll(-Math.max(180, rail.clientWidth * .75)));
    next?.addEventListener('click', () => scroll(Math.max(180, rail.clientWidth * .75)));
    rail.addEventListener('scroll', updateEdges, { passive: true });
    const anchor = sessions.find(item => item.is_anchor) || sessions[0];
    if (current && anchor) {
        current.textContent = anchor.progress_state === 'next' ? '定位下一课' : anchor.progress_state === 'current' ? '定位今天课次' : '定位最近课次';
        current.addEventListener('click', () => {
            select(anchor.order_index);
            const button = buttons.find(item => item.dataset.sessionOrder === String(anchor.order_index));
            reveal(button); button?.focus({ preventScroll: true });
        });
    }
    let drag = null, suppressClick = false;
    const release = () => {
        const old = drag; drag = null;
        if (old) {
            suppressClick = old.moved;
            if (rail.hasPointerCapture(old.id)) rail.releasePointerCapture(old.id);
        }
        rail.classList.remove('is-dragging');
    };
    rail.addEventListener('pointerdown', event => {
        suppressClick = false;
        if (event.pointerType !== 'mouse' || event.button !== 0 || !event.isPrimary) return;
        drag = { id: event.pointerId, x: event.clientX, scroll: rail.scrollLeft, moved: false };
    });
    rail.addEventListener('pointermove', event => {
        if (!drag || drag.id !== event.pointerId) return;
        if (!(event.buttons & 1)) { release(); suppressClick = false; return; }
        const delta = event.clientX - drag.x;
        if (!drag.moved && Math.abs(delta) >= 7) {
            drag.moved = true;
            rail.setPointerCapture(event.pointerId);
            rail.classList.add('is-dragging');
        }
        if (drag.moved) { event.preventDefault(); rail.scrollLeft = drag.scroll - delta; }
    });
    window.addEventListener('pointerup', release);
    rail.addEventListener('pointercancel', () => { release(); suppressClick = false; });
    rail.addEventListener('lostpointercapture', () => { drag = null; rail.classList.remove('is-dragging'); });
    window.addEventListener('blur', () => { release(); suppressClick = false; });
    rail.addEventListener('click', event => {
        if (suppressClick && event.detail !== 0) { event.preventDefault(); event.stopImmediatePropagation(); }
        suppressClick = false;
    }, true);
    buttons.forEach((button, index) => {
        button.addEventListener('click', () => activate(button.dataset.sessionOrder, button));
        button.addEventListener('keydown', event => {
            let target = index;
            if (event.key === 'ArrowLeft') target = Math.max(0, index - 1);
            else if (event.key === 'ArrowRight') target = Math.min(buttons.length - 1, index + 1);
            else if (event.key === 'Home') target = 0;
            else if (event.key === 'End') target = buttons.length - 1;
            else return;
            event.preventDefault();
            buttons.forEach((item, itemIndex) => { item.tabIndex = itemIndex === target ? 0 : -1; });
            buttons[target]?.focus({ preventScroll: true }); reveal(buttons[target]);
        });
    });
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(updateEdges).observe(rail);
    requestAnimationFrame(updateEdges);
    return { reveal, updateEdges };
}

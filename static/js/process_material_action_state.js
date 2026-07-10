export function setActionButtonBusy(button, busy, busyText = '处理中…') {
    if (!button) return;
    if (busy) {
        if (button.dataset.actionBusy === 'true') return;
        button.dataset.actionBusy = 'true';
        button.dataset.originalText = button.textContent || '';
        button.textContent = busyText;
        button.disabled = true;
        button.classList.add('lp-btn--disabled');
        button.setAttribute('aria-disabled', 'true');
        return;
    }
    if ('originalText' in button.dataset) {
        button.textContent = button.dataset.originalText;
        delete button.dataset.originalText;
    }
    delete button.dataset.actionBusy;
    button.disabled = false;
    button.classList.remove('lp-btn--disabled');
    button.removeAttribute('aria-disabled');
}

export async function refreshProcessMaterialActionList(trigger, refresh, onRefreshError) {
    try {
        if (typeof refresh === 'function') await refresh();
    } catch (err) {
        if (typeof onRefreshError === 'function') onRefreshError(err);
    } finally {
        setActionButtonBusy(trigger, false);
    }
}

export function setProcessMaterialModalFormBusy(overlay, busy, { formSelector = '.lp-form' } = {}) {
    const busyState = Boolean(busy);
    overlay?.querySelectorAll('[data-pm-close], [data-lp-close], [data-ap-close], [data-te-close]').forEach((button) => {
        button.disabled = busyState;
    });
    overlay?.querySelectorAll(`${formSelector} input, ${formSelector} select, ${formSelector} textarea, ${formSelector} button`).forEach((control) => {
        control.disabled = busyState;
    });
}

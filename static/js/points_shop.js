// Points shop redemption (growth family, lq_family_enabled('growth') branch only).
// Confirms via LQ.confirm (states the points to be deducted), locks every
// redeem button while a request is in flight (busy re-entrancy guard), and
// only ever writes the balance shown on screen from the server response —
// it is never decremented client-side.
import { LQ } from './lq/index.js';

function init() {
    const statusEl = document.querySelector('[data-redeem-status]');
    const balanceEl = document.querySelector('[data-points-balance]');
    const buttons = Array.from(document.querySelectorAll('[data-redeem-item]'));
    if (!buttons.length) return;
    let busy = false;

    const setBusy = (value) => {
        busy = value;
        buttons.forEach((button) => {
            if (value) button.setAttribute('aria-busy', 'true');
            else button.removeAttribute('aria-busy');
            button.disabled = value;
        });
    };

    buttons.forEach((button) => {
        button.addEventListener('click', async () => {
            if (busy || button.disabled) return;
            const cost = button.getAttribute('data-redeem-cost') || '0';
            const name = button.getAttribute('data-redeem-name') || '该道具';
            const confirmed = await LQ.confirm({
                title: '确认兑换',
                message: `兑换「${name}」将扣除 ${cost} 学分币，兑换后立即生效，不可撤销。`,
                confirmLabel: '确认兑换',
            });
            if (!confirmed) return;
            setBusy(true);
            try {
                const response = await fetch('/api/points/redeem', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ item_key: button.getAttribute('data-redeem-item') }),
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || payload.status !== 'success') {
                    throw new Error(payload.message || payload.detail || '兑换失败，请稍后重试。');
                }
                // Balance is authoritative from the server response only.
                if (balanceEl && payload.balance !== undefined && payload.balance !== null) {
                    balanceEl.textContent = String(payload.balance);
                }
                if (statusEl) {
                    statusEl.textContent = payload.message || '兑换成功。';
                    statusEl.setAttribute('data-tone', 'success');
                }
                await LQ.toast(payload.message || '兑换成功。', { tone: 'success' });
            } catch (error) {
                if (statusEl) {
                    statusEl.textContent = error instanceof Error ? error.message : '兑换失败。';
                    statusEl.setAttribute('data-tone', 'danger');
                }
            } finally {
                // Affordability may have changed after redemption; a full
                // re-render on next navigation re-evaluates `item.affordable`
                // from the server, so this only releases the busy lock.
                setBusy(false);
            }
        });
    });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();

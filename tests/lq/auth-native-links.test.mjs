import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { it, expect } from 'vitest';

// Run the actual controller with only its imported services and DOM boundary
// replaced. Native href/action rendering is verified by the ASGI template tests.
const source = readFileSync(new URL('../../static/js/student_login.js', import.meta.url), 'utf8').replace(/^import .*;\r?\n/gm, '');
function fixture() {
    class Element extends EventTarget {
        dataset = {}; hidden = false; registrations = 0;
        classList = { remove() {}, add() {} };
        addEventListener(...args) { this.registrations++; super.addEventListener(...args); }
    }
    const root = new Element(), first = new Element(), back = new Element(), forgot = new Element();
    first.dataset.switchMode = 'identity'; back.dataset.switchMode = 'password';
    const panels = ['password', 'identity'].map(mode => Object.assign(new Element(), { dataset: { loginPanel: mode } }));
    const opens = [], timers = [];
    const document = {
        readyState: 'complete', querySelector: selector => selector === '[data-student-login-root]' ? root : null,
        querySelectorAll: selector => selector === '[data-login-panel]' ? panels : [first, back],
        getElementById: id => ({ 'first-login-switch': first, 'forgot-password-trigger': forgot })[id] || null,
    };
    const context = vm.createContext({ document, URL, window: {
        location: { hash: '', href: 'http://example.test/student/login?next=%2Fprotected' },
        history: { replaceState() {} }, setTimeout: callback => { timers.push(callback); return timers.length; }, clearTimeout() {},
    }, initLoginScene: async () => null, openModal: id => opens.push(id) });
    vm.runInContext(source, context);
    function click(target, extra = {}) {
        const event = new Event('click', { cancelable: true });
        Object.assign(event, { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...extra });
        target.dispatchEvent(event); return event;
    }
    return { root, first, back, forgot, panels, opens, context, click };
}

it('LQ native auth links are intercepted only by the mounted existing controller', () => {
    const f = fixture();
    expect(f.root.dataset.loginMounted).toBe('true');
    expect(f.click(f.first).defaultPrevented).toBe(true);
    expect(f.panels.map(panel => panel.hidden)).toEqual([true, false]);
    expect(f.click(f.back).defaultPrevented).toBe(true);
    expect(f.panels.map(panel => panel.hidden)).toEqual([false, true]);
    expect(f.click(f.forgot).defaultPrevented).toBe(true);
    expect(f.opens).toEqual(['forgot-password-modal']);
});

it('LQ native auth anchors preserve modifier and auxiliary-click navigation', () => {
    const f = fixture();
    for (const extra of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
        expect(f.click(f.first, extra).defaultPrevented).toBe(false);
        expect(f.click(f.forgot, extra).defaultPrevented).toBe(false);
    }
    expect(f.opens).toEqual([]);
    expect(f.panels.map(panel => panel.hidden)).toEqual([false, true]);
});

it('LQ native link adaptation does not add a second mount or modal owner', () => {
    const f = fixture();
    vm.runInContext('initStudentLogin(); initStudentLogin();', f.context);
    expect([f.first.registrations, f.back.registrations, f.forgot.registrations]).toEqual([1, 1, 1]);
    f.click(f.forgot); expect(f.opens).toEqual(['forgot-password-modal']);
});

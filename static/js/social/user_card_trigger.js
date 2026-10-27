/**
 * 个人信息面板的触发判定 (social doc §12.4)。纯函数 + 一个委托点击监听，无
 * MutationObserver、不扫描 DOM、首屏不加载面板代码。
 *
 * 触发源（只认实名场景）：
 *   - 显式 `data-user-card="role:pk"`（role 为 student/teacher）。值为 "off" 的祖先整块退出。
 *   - 头像 `<img src="/api/profile/avatar?role=..&user_id=..">`。无 role 的地址是“当前用户”，不触发。
 * 不触发：
 *   - 头像位于链接/按钮/表单控件等交互元素内（不劫持原有导航）；显式触发器自身可以是按钮，
 *     但它的祖先不能再是交互元素；
 *   - `[data-user-card="off"]` 子树（课堂讨论别名、匿名内容的承载者按需声明）；
 *   - 博客头像 `[data-blog-avatar]`：笔名/匿名作者面孔绝不挂面板，实名作者从博客的作者浮层进入；
 *   - 面板自身 `[data-user-card-panel]` 与境界标志。
 */

export const CARD_ROLES = Object.freeze(['student', 'teacher']);
export const AVATAR_PATH = '/api/profile/avatar';
export const INTERACTIVE_SELECTOR = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'label', 'summary',
    '[role="button"]', '[role="link"]', '[role="menuitem"]', '[role="tab"]', '[role="option"]', '[contenteditable="true"]',
].join(', ');
const EXCLUDED_SELECTOR = '[data-user-card="off"], [data-user-card-panel], [data-blog-avatar], .lq-cultivation-emblem';

/**
 * @param {unknown} value `role:pk`
 * @returns {string|null} normalised identity or null
 */
export function normalizeCardIdentity(value) {
    const match = /^(student|teacher):(\d{1,12})$/.exec(String(value ?? '').trim());
    if (!match || Number(match[2]) <= 0) return null;
    return `${match[1]}:${Number(match[2])}`;
}

/**
 * @param {string|null|undefined} src avatar image src
 * @param {string} base document base URI
 * @returns {string|null} identity of the face, or null for self/foreign/unknown images
 */
export function avatarIdentity(src, base = 'http://localhost/') {
    if (!src) return null;
    let url;
    try { url = new URL(src, base); } catch { return null; }
    if (url.pathname !== AVATAR_PATH) return null;
    let origin;
    try { origin = new URL(base).origin; } catch { origin = url.origin; }
    if (url.origin !== origin) return null;
    const role = url.searchParams.get('role') || '';
    const userId = url.searchParams.get('user_id') || '';
    if (!CARD_ROLES.includes(role)) return null;
    return normalizeCardIdentity(`${role}:${userId}`);
}

/**
 * Resolve the card trigger for a click target.
 * @param {Element|null} target
 * @returns {{identity: string, trigger: Element}|null}
 */
export function resolveCardTrigger(target) {
    if (!target || typeof target.closest !== 'function') return null;
    const explicit = target.closest('[data-user-card]');
    if (explicit) {
        const identity = normalizeCardIdentity(explicit.getAttribute('data-user-card'));
        if (!identity) return null; // "off" or malformed: the whole subtree opts out
        if (explicit.closest('[data-user-card-panel]')) return null;
        // The trigger may itself be a button; a control *around* it owns the click.
        if (explicit.parentElement?.closest(INTERACTIVE_SELECTOR)) return null;
        // A control nested inside a trigger keeps its own action.
        const inner = target.closest(INTERACTIVE_SELECTOR);
        if (inner && inner !== explicit && explicit.contains(inner)) return null;
        return { identity, trigger: explicit };
    }
    const img = target.closest('img');
    if (!img) return null;
    if (img.closest(EXCLUDED_SELECTOR) || img.closest(INTERACTIVE_SELECTOR)) return null;
    const identity = avatarIdentity(img.getAttribute('src'), img.ownerDocument?.baseURI || 'http://localhost/');
    return identity ? { identity, trigger: img } : null;
}

const BOUND = Symbol.for('lanshare.social.user-card.trigger.v1');

/**
 * One delegated listener per document; the panel module is imported on first use.
 * @param {Document} doc
 * @param {() => any} getClient returns the SocialClient (chat/friend actions)
 */
export function bindUserCardTriggers(doc, getClient) {
    if (!doc || doc[BOUND]) return;
    doc[BOUND] = true;
    let loading = null;
    const open = async (hit) => {
        loading ||= import('./user_card.js');
        try {
            const module = await loading;
            await module.openUserCard(hit.identity, { trigger: hit.trigger, client: getClient() });
        } catch (error) {
            loading = null;
            console.error('[social] user card failed to load', error);
        }
    };
    doc.addEventListener('click', (event) => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const hit = resolveCardTrigger(event.target);
        if (!hit) return;
        event.preventDefault();
        void open(hit);
    });
}

import { button, chip, badge, avatar, spinner, progress, skeleton, html, enhanceComponents } from './components.js';
import { createIcon, iconMarkup } from './icons.js';
import { getLayerSystem } from './layer.js';
import { tone } from './tones.js';

const runtimeKey = Symbol.for('lanshare.lq.runtime.v1');
const chipRow = () => import('./chip-row.js');
const features = { chipRow, forms: () => import('./forms.js'), navigation: () => import('./navigation.js'), dialogs: () => import('./dialogs.js'), collapsible: () => import('./collapsible.js'), tones: () => import('./tones.js'), toast: () => import('./toast.js'), content: () => import('./content.js'), menus: () => import('./menus.js'), tooltips: () => import('./tooltips.js'), status: () => import('./status.js'), tables: () => import('./tables.js'), selection: () => import('./selection.js'), business: () => import('./business.js'), dirtyGuard: () => import('./dirty-guard.js'), upload: () => import('./upload.js'), workspace: () => import('./workspace.js'), insights: () => import('./insights.js'), shells: () => import('./shells.js'), composer: () => import('./composer.js') };

function deferredDialog(api, name, props, options, doc) {
    let current, canceled = false, finish;
    const dismissed = () => name === 'confirm' ? false : { status: 'dismissed' };
    const result = new Promise((resolve, reject) => {
        finish = resolve;
        api.load('dialogs').then(module => {
            if (canceled) return;
            current = module[name](props, options, doc);
            current.then(resolve, reject);
        }).catch(reject);
    });
    Object.defineProperties(result, {
        handle: { get: () => current?.handle ?? null },
        destroy: { value: () => { if (canceled) return; canceled = true; if (current) current.destroy(); else finish(dismissed()); } },
    });
    return result;
}

function deferredController(api, feature, method, root, options) {
    let current, canceled = false, finish;
    const result = new Promise((resolve, reject) => {
        finish = resolve;
        api.load(feature).then(module => {
            if (canceled) return;
            if (!root?.isConnected) { resolve(null); return; }
            current = module[method](root, options); resolve(current);
        }).catch(reject);
    });
    Object.defineProperties(result, {
        handle: { get: () => current ?? null },
        destroy: { value: () => { canceled = true; current?.destroy(); finish(null); } },
    });
    return result;
}

/** SSR installs the tiny ready queue before page scripts. Standalone ESM users
 * get the same contract without needing a document template. */
export function getLQ(doc = document) {
    let runtime = doc[runtimeKey];
    if (!runtime) {
        let resolve;
        const loaded = new Promise(done => { resolve = done; });
        const api = { ready(callback) {
            if (callback !== undefined && typeof callback !== 'function') throw new TypeError('LQ.ready expects a function');
            return loaded.then(() => callback ? callback(api) : api);
        } };
        runtime = { api, resolve, installed: false };
        doc[runtimeKey] = runtime;
    }
    const { api } = runtime;
    if (!runtime.installed) {
        runtime.installed = true;
        const modules = new Map();
        Object.assign(api, {
            load(name) {
                if (!Object.hasOwn(features, name)) return Promise.reject(new TypeError('Unknown LQ feature'));
                if (!modules.has(name)) modules.set(name, features[name]().catch(error => { modules.delete(name); throw error; }));
                return modules.get(name);
            },
            confirm: (props, options) => deferredDialog(api, 'confirm', props, options, doc),
            choose: (props, options) => deferredDialog(api, 'choose', props, options, doc),
            toast: (message, options) => api.load('toast').then(module => module.toast(message, options, doc)),
            tabs: (root, options) => deferredController(api, 'navigation', 'tabs', root, options),
            segment: (root, options) => deferredController(api, 'navigation', 'segment', root, options),
            collapsible: (root, options) => deferredController(api, 'collapsible', 'enhanceCollapsible', root, options),
            tone,
            button: props => button(props, doc), chip: props => chip(props, doc),
            badge: props => badge(props, doc), avatar: props => avatar(props, doc),
            spinner: props => spinner(props, doc), progress: props => progress(props, doc),
            skeleton: props => skeleton(props, doc),
            icon: name => createIcon(name, doc), html: Object.freeze({ ...html, icon: iconMarkup }),
        });
        // A destroyed coordinator may be explicitly recreated; never retain a
        // stale layer instance in the global facade.
        Object.defineProperty(api, 'layer', { enumerable: true, get: () => getLayerSystem(doc) });
        const start = () => {
            runtime.enhancements = enhanceComponents(doc);
            runtime.resolve();
        };
        if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', start, { once: true });
        else start();
    }
    if (doc.defaultView) doc.defaultView.LQ = api;
    return api;
}

export { button, chip, badge, avatar, spinner, progress, skeleton, html, createIcon, iconMarkup, getLayerSystem, tone };
export const LQ = typeof document === 'undefined' ? null : getLQ(document);
export default LQ;

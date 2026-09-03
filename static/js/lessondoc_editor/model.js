/* JSON helpers shared by commands, property editors and the iframe bridge. */
export const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
export const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
export function uid(prefix = 'b') {
    return prefix + (globalThis.crypto?.randomUUID?.().replaceAll('-', '') || Date.now().toString(36) + Math.random().toString(36).slice(2));
}
const BLOCK_ARRAYS = ['blocks', 'left', 'right', 'objects', 'overlays', 'globals'];
export function walkBlocks(node, visit, path = [], ancestors = []) {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach((item, i) => walkBlocks(item, visit, [...path, i], ancestors)); return; }
    if (typeof node.type === 'string') {
        visit(node, path, ancestors);
        ancestors = [...ancestors, node];
    }
    for (const key of BLOCK_ARRAYS) if (Array.isArray(node[key])) walkBlocks(node[key], visit, [...path, key], ancestors);
    // Diagram children are data nodes, not editable blocks.
    if (node.type === 'group') walkBlocks(node.children, visit, [...path, 'children'], ancestors);
    if (node.type === 'stepper') walkBlocks(node.stage, visit, [...path, 'stage'], ancestors);
    for (const key of ['slides', 'tabs', 'sections', 'areas']) if (Array.isArray(node[key])) walkBlocks(node[key], visit, [...path, key], ancestors);
    if (node.home) walkBlocks(node.home, visit, [...path, 'home'], ancestors);
}
export function at(root, path) { return path.reduce((node, key) => node?.[key], root); }
export function locate(root, id) {
    let found;
    walkBlocks(root, (block, path, ancestors) => { if (block.id === id) found = { block, path, ancestors }; });
    return found;
}
export function setAt(root, path, value) {
    let parent = root;
    path.slice(0, -1).forEach((key, i) => { parent = parent[key] ??= typeof path[i + 1] === 'number' ? [] : {}; });
    if (value === undefined) delete parent[path.at(-1)]; else parent[path.at(-1)] = value;
}
export function freshInstance(source, { navigation = true } = {}) {
    const copy = clone(source), mapping = new Map();
    if (copy.slides) for (const slide of copy.slides) { const old = slide.id; slide.id = uid('s'); if (old) mapping.set(old, slide.id); }
    walkBlocks(copy, (block) => { const old = block.id; block.id = uid(); if (old) mapping.set(old, block.id); });
    walkBlocks(copy, (block) => {
        if (!block.actions) return;
        block.actions = block.actions.filter((action) => navigation || !['goto', 'next', 'prev'].includes(action.do));
        for (const action of block.actions) {
            if (mapping.has(action.target)) action.target = mapping.get(action.target);
            if (mapping.has(action.slideId)) action.slideId = mapping.get(action.slideId);
        }
    });
    return copy;
}
export function rootSelection(model, ids) {
    const selected = new Set(ids);
    return ids.map((id) => locate(model, id)).filter((item) => item && !item.ancestors.some((block) => selected.has(block.id)));
}
export function removeBlocks(model, ids) {
    const doomed = new Set();
    const roots = rootSelection(model, ids);
    for (const item of roots) walkBlocks(item.block, (b) => doomed.add(b.id));
    // Delete higher indexes first; a nested stage must be removed with its stepper.
    for (const item of roots.sort((a, b) => String(b.path.slice(0, -1)).localeCompare(String(a.path.slice(0, -1))) || Number(b.path.at(-1)) - Number(a.path.at(-1)))) {
        const parent = at(model, item.path.slice(0, -1));
        if (!Array.isArray(parent)) throw new Error('舞台是分步演示的一部分，请删除整个分步演示，或更换舞台内容。');
        parent.splice(item.path.at(-1), 1);
    }
    let actionsRemoved = 0;
    walkBlocks(model, (b) => { if (b.actions) b.actions = b.actions.filter((a) => { if (!doomed.has(a.target)) return true; actionsRemoved++; return false; }); });
    for (const slide of model.slides || []) if (!['title', 'section', 'end'].includes(slide.layout)) slide.empty = true;
    return actionsRemoved;
}
export function insertionList(model, slideIndex = 0, scope = 'page') {
    if (model.kind === 'home') {
        if(scope.startsWith('tab:')){const tab=model.tabs?.[Number(scope.slice(4))];if(!tab)throw new Error('该标签页已移除，请重新选择插入位置。');return tab.blocks??=[];}
        model.home ??= {}; model.home.sections ??= ['hero', 'mindmap', 'nav', 'blocks', 'tabs', 'footer'].map((key) => ({ key }));
        const section = model.home.sections.find((s) => s.key === 'blocks');
        if (!section) throw new Error('首页说明区块不存在');
        return section.blocks ??= [];
    }
    if (scope === 'global') return model.globals ??= [];
    const slide = model.slides[slideIndex];
    if (scope === 'overlay') return slide.overlays ??= [];
    if (slide.layout === 'canvas') return slide.objects ??= [];
    if (slide.layout === 'two-col') { slide.left ??= []; return slide.left; }
    if (slide.layout === 'grid') { slide.areas ??= [{blocks:[]}]; if (!slide.areas.length) slide.areas.push({blocks:[]}); return slide.areas[0].blocks ??= []; }
    return slide.blocks ??= [];
}

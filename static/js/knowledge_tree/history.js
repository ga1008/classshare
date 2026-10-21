// Bounded, per-dialog edit history. Only confirmed saves advance the baseline.
// Drafts remain in memory and never leak into another teacher/session's storage.
const copy = value => JSON.parse(JSON.stringify(value));

export function treeSnapshot(state) {
    const tree = copy(state.tree);
    delete tree.edited_at;
    delete tree.depth;
    return { tree, completed_ids: [...(state.completed_ids || [])].sort() };
}

const key = snapshot => JSON.stringify(snapshot);

export function createEditHistory(limit = 80) {
    let baseline = null;
    const steps = [];
    return {
        reset(state) { baseline = treeSnapshot(state); steps.length = 0; },
        saved(state) { baseline = treeSnapshot(state); },
        dirty(state) { return Boolean(baseline && key(treeSnapshot(state)) !== key(baseline)); },
        push(before, after, label) {
            const previous = treeSnapshot(before);
            if (key(previous) === key(treeSnapshot(after))) return false;
            steps.push({ ...previous, label });
            if (steps.length > limit) steps.shift();
            return true;
        },
        undo() { return steps.pop() || null; },
        get size() { return steps.length; },
        get label() { return steps.at(-1)?.label || ''; },
    };
}

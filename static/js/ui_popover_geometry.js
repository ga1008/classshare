/** Shared geometry and logical-parent lookup; no document listeners or ownership. */
export function findPopoverParent(stack, anchor, getRoot = (item) => item.panel) {
    if (!anchor) return null;
    for (let index = stack.length - 1; index >= 0; index--) {
        if (getRoot(stack[index])?.contains(anchor)) return stack[index];
    }
    return null;
}

export function getPopoverPosition({ anchor, panel, width, height, placement = 'bottom-start', margin = 12, gap = 8 }) {
    const [side, align = 'start'] = placement.split('-');
    const horizontal = side === 'left' || side === 'right';
    let left = align === 'end' ? anchor.right - panel.width : anchor.left;
    let top = anchor.bottom + gap;
    let flipped = false;
    if (horizontal) {
        top = align === 'end' ? anchor.bottom - panel.height : anchor.top;
        left = side === 'left' ? anchor.left - gap - panel.width : anchor.right + gap;
        const other = side === 'left' ? anchor.right + gap : anchor.left - gap - panel.width;
        if ((left < margin || left + panel.width > width - margin) && other >= margin && other + panel.width <= width - margin) {
            left = other; flipped = true;
        }
    } else {
        if (side === 'top') top = anchor.top - gap - panel.height;
        const other = side === 'top' ? anchor.bottom + gap : anchor.top - gap - panel.height;
        if ((top < margin || top + panel.height > height - margin) && other >= margin && other + panel.height <= height - margin) {
            top = other; flipped = true;
        }
    }
    return {
        left: Math.round(Math.max(margin, Math.min(left, width - panel.width - margin))),
        top: Math.round(Math.max(margin, Math.min(top, height - panel.height - margin))), flipped,
    };
}

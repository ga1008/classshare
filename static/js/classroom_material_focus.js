// Focus ownership for the three legacy classroom material dialogs only.
// Nested confirmation suspends the list; closing restores the previous owner.
const owners = [];
const controls = 'button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])';
const visible = node => node instanceof HTMLElement && !node.closest('[inert]') && node.getClientRects().length > 0;
const focusables = owner => [...owner.overlay.querySelectorAll(controls)].filter(visible);
const top = () => owners[owners.length - 1];

function focusFirst(owner) {
    (focusables(owner)[0] || owner.dialog).focus({ preventScroll: true });
}
function onKeydown(event) {
    const owner = top();
    if (!owner) return;
    if (event.key === 'Escape') {
        event.preventDefault(); event.stopImmediatePropagation(); owner.close();
    } else if (event.key === 'Tab') {
        const nodes = focusables(owner), first = nodes[0], last = nodes[nodes.length - 1];
        if (!first || !owner.overlay.contains(document.activeElement)) {
            event.preventDefault(); focusFirst(owner);
        } else if (event.shiftKey && document.activeElement === first) {
            event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first.focus();
        }
    }
}
function onFocus(event) {
    const owner = top();
    if (owner && !owner.overlay.contains(event.target)) focusFirst(owner);
}

export function ownClassroomMaterialFocus(overlay, close, initialFocus, returnFocus) {
    const returnTo = returnFocus || document.activeElement;
    const overlayInert = overlay.inert;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    overlay.inert = false;
    const dialog = overlay.querySelector('[role="dialog"],[role="alertdialog"],.modal-dialog') || overlay;
    dialog.tabIndex = -1;
    const inert = [];
    for (let branch = overlay; branch && branch !== document.body; branch = branch.parentElement) {
        for (const sibling of branch.parentElement?.children || []) {
            if (sibling !== branch && sibling instanceof HTMLElement && !['SCRIPT', 'STYLE', 'LINK'].includes(sibling.tagName)) {
                inert.push([sibling, sibling.inert]); sibling.inert = true;
            }
        }
    }
    const owner = { overlay, dialog, close, returnTo, inert };
    owners.push(owner);
    overlay.dataset.materialFocusOwner = 'true';
    if (owners.length === 1) {
        document.addEventListener('keydown', onKeydown, true);
        document.addEventListener('focusin', onFocus, true);
    }
    (visible(initialFocus) ? initialFocus : focusables(owner)[0] || dialog).focus({ preventScroll: true });
    let released = false;
    return () => {
        if (released) return;
        released = true;
        owners.splice(owners.indexOf(owner), 1);
        delete overlay.dataset.materialFocusOwner;
        inert.forEach(([node, previous]) => { node.inert = previous; });
        overlay.inert = overlayInert;
        document.body.style.overflow = previousOverflow;
        if (!owners.length) {
            document.removeEventListener('keydown', onKeydown, true);
            document.removeEventListener('focusin', onFocus, true);
        }
        if (visible(returnTo)) returnTo.focus({ preventScroll: true });
        else if (top()) focusFirst(top());
    };
}

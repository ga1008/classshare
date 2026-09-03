/** Compatibility entry point: retain whiteboard classes and one-open behavior. */
import { createPopoverSystem } from '../ui_popover.js';
export { POPOVER_TIMING } from '../ui_popover.js';
export const { createPopover, popoverManager } = createPopoverSystem({ prefix: 'twb' });

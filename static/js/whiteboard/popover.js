/** Compatibility entry point: retain whiteboard classes and one-open behavior. */
import { createDomainPopoverSystem } from '../lq/domain-controls.js';
export { POPOVER_TIMING } from '../ui_popover.js';
export const { createPopover, popoverManager } = createDomainPopoverSystem({ prefix: 'twb' });

import { legacyModuleUrl } from '@/lib/static-assets';

export type LayerReason = 'button' | 'escape' | 'outside' | 'back' | 'parent-destroyed' | 'programmatic';
export type LayerHandle = {
  root: HTMLElement;
  state: 'opening' | 'open' | 'checking' | 'closing' | 'closed' | 'destroyed';
  update: (options: Partial<LayerOptions>) => LayerHandle;
  destroy: () => void;
};
export type LayerOptions = {
  type?: 'modal' | 'sheet' | 'drawer' | 'popover' | 'menu' | 'viewer';
  modality?: 'modal' | 'non-modal';
  owner?: HTMLElement;
  surface?: HTMLElement;
  trigger?: HTMLElement | null;
  parentLayer?: LayerHandle | null;
  returnFocus?: HTMLElement | false | null | (() => HTMLElement | null);
  beforeClose?: (reason: LayerReason, handle: LayerHandle) => unknown;
  onInitialFocus?: (event: Event, handle: LayerHandle) => void;
  onReturnFocus?: (event: Event, handle: LayerHandle) => void;
  onCloseRequested?: (reason: LayerReason, handle: LayerHandle) => void;
  onClose?: (reason: LayerReason, handle: LayerHandle) => void;
  onDestroy?: (reason: string, handle: LayerHandle) => void;
};
export type LayerSystem = {
  open: (root: HTMLElement, options: LayerOptions) => LayerHandle;
  close: (handle: LayerHandle, reason?: LayerReason) => Promise<boolean>;
  top: () => LayerHandle | null;
  getPortalHost: (options?: { trigger?: HTMLElement | null; parentLayer?: LayerHandle | null }) => HTMLElement;
};
type LayerModule = { getLayerSystem: (doc: Document) => LayerSystem };
let modulePromise: Promise<LayerModule> | undefined;

/** Native imports stay on the document's graph. Its Document Symbol owns the
 * singleton; the React bundle never constructs another layer stack. */
export function loadLayerSystem(doc: Document): Promise<LayerSystem> {
  if (!modulePromise) {
    const pending = import(/* @vite-ignore */ legacyModuleUrl('lq/layer.js')) as Promise<LayerModule>;
    modulePromise = pending;
    void pending.catch(() => { if (modulePromise === pending) modulePromise = undefined; });
  }
  return modulePromise.then(module => module.getLayerSystem(doc));
}

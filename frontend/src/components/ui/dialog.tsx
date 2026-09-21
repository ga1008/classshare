import * as React from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { loadLayerSystem, type LayerHandle, type LayerOptions, type LayerReason, type LayerSystem } from '@/lib/lq-layer';

type DialogContextValue = {
  open: boolean;
  modal: boolean;
  setOpen: (open: boolean) => void;
  titleId: string;
  descriptionId: string;
  described: boolean;
  setDescribed: (value: boolean) => void;
};
const DialogContext = React.createContext<DialogContextValue | null>(null);
const useDialog = () => {
  const value = React.useContext(DialogContext);
  if (!value) throw new Error('Dialog components require a Dialog parent');
  return value;
};

type DialogProps = React.PropsWithChildren<{ open: boolean; onOpenChange: (open: boolean) => void; modal?: boolean }>;
function Dialog({ open, onOpenChange, modal = true, children }: DialogProps) {
  const id = React.useId();
  const [described, setDescribed] = React.useState(false);
  return <DialogContext.Provider value={{ open, modal, setOpen: onOpenChange, titleId: `${id}-title`, descriptionId: `${id}-description`, described, setDescribed }}>
    {children}
  </DialogContext.Provider>;
}

type DialogContentProps = React.HTMLAttributes<HTMLDivElement> & {
  onOpenAutoFocus?: (event: Event) => void;
  onCloseAutoFocus?: (event: Event) => void;
  onAfterClose?: (reason: LayerReason) => void;
  beforeClose?: LayerOptions['beforeClose'];
  returnFocus?: LayerOptions['returnFocus'];
  parentLayer?: LayerHandle | null;
};
type Portal = { system: LayerSystem; host: HTMLElement; trigger: HTMLElement | null };

/** React owns presence and children; the native coordinator exclusively owns
 * focus, dismissal, inert and scroll locks. No Radix runtime is mounted. */
const DialogContent = React.forwardRef<HTMLDivElement, DialogContentProps>(function DialogContent({
  className, children, onOpenAutoFocus, onCloseAutoFocus, onAfterClose, beforeClose, returnFocus, parentLayer, ...props
}, forwardedRef) {
  const dialog = useDialog();
  const latest = React.useRef({ dialog, onOpenAutoFocus, onCloseAutoFocus, onAfterClose, beforeClose, returnFocus, parentLayer });
  latest.current = { dialog, onOpenAutoFocus, onCloseAutoFocus, onAfterClose, beforeClose, returnFocus, parentLayer };
  const [portal, setPortal] = React.useState<Portal | null>(null);
  const root = React.useRef<HTMLDivElement>(null);
  const surface = React.useRef<HTMLDivElement>(null);
  const overlay = React.useRef<HTMLDivElement>(null);
  const handle = React.useRef<LayerHandle | null>(null);
  const alive = React.useRef(false);
  const loadGeneration = React.useRef(0);
  const phase = React.useRef<'open' | 'closed'>('open');
  const pendingCompletion = React.useRef<LayerReason | null>(null);
  const autoFocusNotified = React.useRef(false);
  const lastOpen = React.useRef(false);
  const registeredParent = React.useRef(parentLayer);
  const closeRequest = React.useRef<{ handle: LayerHandle; promise: Promise<boolean> } | null>(null);
  const setSurfaceRef = React.useCallback((node: HTMLDivElement | null) => {
    surface.current = node;
    if (typeof forwardedRef === 'function') {
      const cleanup = (forwardedRef as React.RefCallback<HTMLDivElement>)(node);
      if (typeof cleanup === 'function') return () => { surface.current = null; cleanup(); };
    } else if (forwardedRef) forwardedRef.current = node;
  }, [forwardedRef]);

  const setPhase = (value: 'open' | 'closed') => {
    phase.current = value;
    for (const node of [surface.current, overlay.current]) if (node) node.dataset.state = value;
  };
  const notifyCloseFocus = (event: Event) => {
    if (autoFocusNotified.current) return;
    autoFocusNotified.current = true;
    latest.current.onCloseAutoFocus?.(event);
  };
  React.useLayoutEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      loadGeneration.current++;
      pendingCompletion.current = null;
      closeRequest.current = null;
      const previous = handle.current;
      handle.current = null;
      previous?.destroy();
    };
  }, []);

  React.useLayoutEffect(() => {
    const generation = ++loadGeneration.current;
    if (!dialog.open || portal) return;
    pendingCompletion.current = null;
    const doc = document;
    const trigger = doc.activeElement instanceof HTMLElement ? doc.activeElement : null;
    void loadLayerSystem(doc).then(system => {
      if (!alive.current || generation !== loadGeneration.current || !latest.current.dialog.open) return;
      const parent = latest.current.parentLayer;
      setPortal({ system, host: system.getPortalHost({ trigger, parentLayer: parent }), trigger });
    }).catch(error => {
      if (!alive.current || generation !== loadGeneration.current) return;
      console.error('[lq-dialog] layer failed to load', error);
      latest.current.dialog.setOpen(false);
    });
    return () => { loadGeneration.current++; };
  }, [dialog.open, portal]);

  React.useLayoutEffect(() => {
    const reopened = dialog.open && !lastOpen.current;
    lastOpen.current = dialog.open;
    if (!portal || !root.current || !surface.current) {
      const completed = pendingCompletion.current;
      pendingCompletion.current = null;
      if (completed && !dialog.open) latest.current.onAfterClose?.(completed);
      return;
    }
    if (handle.current && registeredParent.current !== parentLayer) {
      const previous = handle.current;
      handle.current = null;
      closeRequest.current = null;
      previous.destroy();
      setPortal(null);
      return;
    }
    const options: LayerOptions = {
      type: 'modal', modality: dialog.modal ? 'modal' : 'non-modal',
      owner: root.current, surface: surface.current, trigger: portal.trigger,
      ...(parentLayer !== undefined ? { parentLayer } : {}),
      returnFocus: () => {
        const target = latest.current.returnFocus;
        return typeof target === 'function' ? target() : target === false ? null : target || portal.trigger;
      },
      beforeClose: (reason, layer) => latest.current.beforeClose?.(reason, layer),
      onInitialFocus: event => latest.current.onOpenAutoFocus?.(event),
      onReturnFocus: event => {
        if (latest.current.returnFocus === false) event.preventDefault();
        notifyCloseFocus(event);
      },
      onCloseRequested: (_reason, layer) => {
        if (!alive.current || handle.current !== layer) return;
        setPhase('closed');
        latest.current.dialog.setOpen(false);
      },
      onClose: (reason, layer) => {
        if (!alive.current || handle.current !== layer) return;
        // The coordinator intentionally skips return-focus for an outside close.
        // Consumers still receive the notification, without default focus.
        notifyCloseFocus(new Event('lq:close-autofocus', { cancelable: true }));
        handle.current = null;
        closeRequest.current = null;
        pendingCompletion.current = reason;
        setPortal(null);
      },
      onDestroy: (_reason, layer) => {
        if (!alive.current || handle.current !== layer) return;
        handle.current = null;
        closeRequest.current = null;
        pendingCompletion.current = null;
        latest.current.dialog.setOpen(false);
        setPortal(null);
      },
    };
    if (dialog.open) {
      pendingCompletion.current = null;
      closeRequest.current = null;
      if (!handle.current || (reopened && ['checking', 'closing'].includes(handle.current.state))) {
        autoFocusNotified.current = false;
        setPhase('open');
        registeredParent.current = parentLayer;
        handle.current = portal.system.open(root.current, options);
      } else handle.current.update(options);
    } else if (handle.current) {
      handle.current.update(options);
      const current = handle.current;
      const promise = portal.system.close(current, 'programmatic');
      if (closeRequest.current?.promise !== promise) {
        const request = { handle: current, promise };
        closeRequest.current = request;
        void promise.then(closed => {
          if (closeRequest.current !== request) return;
          closeRequest.current = null;
          // A controlled programmatic close may be vetoed too. Reflect the
          // retained layer back to its owner, never resurrect a superseded exit.
          if (!closed && alive.current && handle.current === current && current.state === 'open' && !latest.current.dialog.open) {
            latest.current.dialog.setOpen(true);
          }
        });
      }
    } else setPortal(null);
  });

  if (!portal) return null;
  return createPortal(<div ref={root} data-ui-dialog-root="">
    {dialog.modal && <div ref={overlay} data-ui-dialog-overlay="" data-ui-overlay-surface="" data-state={phase.current} className="tw-fixed tw-inset-0 tw-z-50 tw-bg-black/80" />}
    <div {...props} ref={setSurfaceRef} role="dialog" tabIndex={-1} data-ui-dialog-content="" data-state={phase.current}
      aria-labelledby={props['aria-labelledby'] || dialog.titleId}
      aria-describedby={Object.hasOwn(props, 'aria-describedby') ? props['aria-describedby'] : dialog.described ? dialog.descriptionId : undefined}
      className={cn('tw-fixed tw-left-[50%] tw-top-[50%] tw-z-50 tw-grid tw-w-full tw-max-w-lg tw-translate-x-[-50%] tw-translate-y-[-50%] tw-gap-4 tw-border tw-bg-background tw-p-6 tw-shadow-lg sm:tw-rounded-lg', className)}>
      {children}
      <button type="button" className="ui-dialog-close tw-absolute tw-right-4 tw-top-4 tw-flex tw-h-8 tw-w-8 tw-items-center tw-justify-center tw-rounded-sm tw-opacity-70 tw-ring-offset-background tw-transition-opacity hover:tw-opacity-100 focus:tw-outline-none focus:tw-ring-2 focus:tw-ring-ring focus:tw-ring-offset-2 disabled:tw-pointer-events-none" onClick={() => { if (handle.current) void portal.system.close(handle.current, 'button'); }}>
        <X className="tw-h-4 tw-w-4" aria-hidden="true" /><span className="tw-sr-only">关闭</span>
      </button>
    </div>
  </div>, portal.host);
});

const DialogHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn('tw-flex tw-flex-col tw-space-y-1.5 tw-text-center sm:tw-text-left', className)} {...props} />;
const DialogFooter = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn('tw-flex tw-flex-col-reverse sm:tw-flex-row sm:tw-justify-end sm:tw-space-x-2', className)} {...props} />;
const DialogTitle = React.forwardRef<HTMLHeadingElement, React.HTMLAttributes<HTMLHeadingElement>>(function DialogTitle({ className, ...props }, ref) {
  const dialog = useDialog();
  return <h2 id={dialog.titleId} ref={ref} className={cn('tw-text-lg tw-font-semibold tw-leading-none tw-tracking-tight', className)} {...props} />;
});
const DialogDescription = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLParagraphElement>>(function DialogDescription({ className, ...props }, ref) {
  const dialog = useDialog();
  React.useLayoutEffect(() => {
    dialog.setDescribed(true);
    return () => dialog.setDescribed(false);
  }, [dialog.setDescribed]);
  return <p id={dialog.descriptionId} ref={ref} className={cn('tw-text-sm tw-text-muted-foreground', className)} {...props} />;
});

export { Dialog, DialogContent, DialogHeader, DialogFooter, DialogTitle, DialogDescription };

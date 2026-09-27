import * as React from 'react';
import { componentTree, type PresentationTree } from '../../../static/js/lq/components.js';
// These pure native descriptors own the DOM and accessibility contract.
// @ts-expect-error The shared native form module is intentionally plain JavaScript.
import { formProps } from '../../../static/js/lq/forms.js';
// @ts-expect-error The shared native content module is intentionally plain JavaScript.
import { contentProps } from '../../../static/js/lq/content.js';
// @ts-expect-error The shared native status module is intentionally plain JavaScript.
import { statusProps } from '../../../static/js/lq/status.js';
import { ICON_NODES, ICON_ALIASES } from '../../../static/js/lq/icons.generated.js';

type Attributes = Record<string, string | number | boolean | null | undefined>;
type ButtonOptions = {
  label?: string; icon?: string; href?: string; id?: string;
  variant?: 'prominent' | 'glass' | 'soft' | 'ghost' | 'destructive' | 'link';
  size?: 'sm' | 'md' | 'lg'; type?: 'button' | 'submit' | 'reset';
  disabled?: boolean; ariaDisabled?: boolean; loading?: boolean; badge?: string | number;
  attrs?: Attributes;
};
export type LqButtonProps = ButtonOptions & {
  children?: React.ReactNode;
  className?: string;
  onClick?: React.MouseEventHandler<HTMLElement>;
  onClickCapture?: React.MouseEventHandler<HTMLElement>;
  onKeyDown?: React.KeyboardEventHandler<HTMLElement>;
  onKeyDownCapture?: React.KeyboardEventHandler<HTMLElement>;
  /** A trusted React icon is only a compatibility slot for existing launchers. */
  iconContent?: React.ReactNode;
  iconClassName?: string;
  /** Existing launcher DOM callbacks/styles remain attached to the native root. */
  nativeProps?: Omit<React.HTMLAttributes<HTMLElement>, 'children' | 'dangerouslySetInnerHTML'>;
  labelContent?: React.ReactNode;
};

const reactName: Record<string, string> = { class: 'className', for: 'htmlFor', tabindex: 'tabIndex', readonly: 'readOnly', 'stroke-width': 'strokeWidth',
  'stroke-linecap': 'strokeLinecap', 'stroke-linejoin': 'strokeLinejoin' };
const ownedRootProps = new Set(['children', 'dangerouslySetInnerHTML', 'href', 'type', 'disabled',
  'aria-label', 'aria-labelledby', 'aria-hidden', 'aria-live', 'aria-busy', 'aria-disabled', 'data-lq-disabled']);
function reactAttributes(attrs: Record<string, string>) {
  return Object.fromEntries(Object.entries(attrs).map(([key, value]) =>
    [reactName[key] || key, ['disabled', 'required', 'readonly', 'hidden'].includes(key) ? true : value]));
}
function plainLabel(children: React.ReactNode): string {
  return React.Children.toArray(children).map(child => typeof child === 'string' || typeof child === 'number' ? String(child)
    : React.isValidElement<{ children?: React.ReactNode }>(child) ? plainLabel(child.props.children) : '').join('');
}
function icon(name: string) {
  const key = Object.hasOwn(ICON_ALIASES, name) ? ICON_ALIASES[name] : name;
  const nodes = ICON_NODES[Object.hasOwn(ICON_NODES, key) ? key : 'circle-question-mark'];
  return <svg className="lq-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {nodes.map(([tag, attrs], index) => React.createElement(tag, { ...reactAttributes(attrs), key: index }))}
  </svg>;
}
function renderTree(node: PresentationTree, options: { iconContent?: React.ReactNode; iconClassName?: string; labelContent?: React.ReactNode } = {}): React.ReactNode {
  if (node === null || typeof node === 'string') return node;
  if ('icon' in node) return options.iconContent === undefined ? icon(node.icon) : options.iconContent;
  const props: Record<string, unknown> = reactAttributes(node.attrs);
  if (node.attrs.class === 'lq-btn__icon' && options.iconClassName) props.className += ` ${options.iconClassName}`;
  if (node.tag === 'img') {
    // Changing src creates a fresh image, so one failed URL cannot hide its replacement.
    props.key = node.attrs.src;
    props.onError = (event: React.SyntheticEvent<HTMLImageElement>) => { event.currentTarget.hidden = true; };
    return React.createElement(node.tag, props);
  }
  return React.createElement(node.tag, props, ...(node.attrs.class === 'lq-btn__label' && options.labelContent !== undefined
    ? [options.labelContent] : node.children.map(child => renderTree(child, options))));
}

/** Props, DOM shape, escaping and state semantics come from the native module.
 * React owns its event handlers and ref; it installs no document listeners. */
export const LqButton = React.forwardRef<HTMLElement, LqButtonProps>(function LqButton({
  className, children, onClick, onClickCapture, onKeyDown, onKeyDownCapture, iconContent, iconClassName, nativeProps, labelContent, ...options
}, ref) {
  const childLabel = children !== undefined && typeof options.attrs?.['aria-label'] === 'string' ? options.attrs['aria-label'] : plainLabel(children);
  const tree = componentTree('button', { ...options, label: options.label ?? childLabel });
  if (!tree || typeof tree === 'string' || 'icon' in tree) throw new TypeError('Invalid LQ button tree');
  const blocked = tree.attrs['data-lq-disabled'] === 'true';
  const clickCapture: React.MouseEventHandler<HTMLElement> = event => {
    if (blocked) { event.preventDefault(); event.stopPropagation(); return; }
    (onClickCapture || nativeProps?.onClickCapture)?.(event);
  };
  const keyCapture: React.KeyboardEventHandler<HTMLElement> = event => {
    if (blocked && ['Enter', ' '].includes(event.key)) { event.preventDefault(); event.stopPropagation(); return; }
    (onKeyDownCapture || nativeProps?.onKeyDownCapture)?.(event);
  };
  const compatible = Object.fromEntries(Object.entries(nativeProps || {}).filter(([key]) => !ownedRootProps.has(key)));
  return React.createElement(tree.tag, { ...compatible, ...reactAttributes(tree.attrs),
    className: [tree.attrs.class, className].filter(Boolean).join(' '), ref, 'data-lq-component': 'button',
    onClick: onClick || nativeProps?.onClick, onClickCapture: clickCapture,
    onKeyDown: onKeyDown || nativeProps?.onKeyDown, onKeyDownCapture: keyCapture,
  }, ...tree.children.map(child => renderTree(child, { iconContent, iconClassName, labelContent: labelContent ?? children })));
});

export type LqAvatarProps = { name: string; src?: string; size?: 24 | 32 | 40 | 56; attrs?: Attributes; className?: string };
export const LqAvatar = React.forwardRef<HTMLSpanElement, LqAvatarProps>(function LqAvatar({ className, ...options }, ref) {
  const tree = componentTree('avatar', options);
  if (!tree || typeof tree === 'string' || 'icon' in tree) throw new TypeError('Invalid LQ avatar tree');
  return React.createElement('span', { ...reactAttributes(tree.attrs), ref,
    className: [tree.attrs.class, className].filter(Boolean).join(' '),
  }, ...tree.children.map(child => renderTree(child)));
});

type Descriptor = { tag: string; attrs: Record<string, string>; children: Array<Descriptor | string | { slot: string }> };
type NativeFieldOptions = { label: string; fieldClassName?: string; help?: string; error?: string; controlSize?: 'sm' | 'md' | 'lg' };
type NativeControl = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
type NativeControlProps = React.InputHTMLAttributes<HTMLInputElement> | React.TextareaHTMLAttributes<HTMLTextAreaElement> | React.SelectHTMLAttributes<HTMLSelectElement>;
/** The native form descriptor supplies Field/label/help/error and the control.
 * React alone owns values, refs and callbacks, including native submission. */
export function LqField({ kind, label, fieldClassName, help, error, controlSize = 'md', nativeProps, controlRef }:
  NativeFieldOptions & { kind: 'input' | 'textarea' | 'select'; nativeProps: NativeControlProps; controlRef?: React.Ref<NativeControl> }) {
  const generatedId = React.useId();
  const identity = nativeProps.id || `lq-react-${generatedId.replace(/[^A-Za-z0-9_-]/g, '')}`;
  const { children, className, ...controlProps } = nativeProps;
  const tree = formProps(kind, { id: identity, label, help, error, size: controlSize,
    type: kind === 'input' ? (nativeProps as React.InputHTMLAttributes<HTMLInputElement>).type : undefined,
    disabled: Boolean(nativeProps.disabled), required: Boolean(nativeProps.required),
    readOnly: kind === 'select' ? false : Boolean((nativeProps as React.InputHTMLAttributes<HTMLInputElement>).readOnly),
    attrs: { 'aria-describedby': nativeProps['aria-describedby'] } }) as Descriptor;
  function render(node: Descriptor | string | { slot: string }, root = false): React.ReactNode {
    if (typeof node === 'string') return node;
    if ('slot' in node) return null;
    const props = reactAttributes(node.attrs);
    if (node.attrs.id === identity) {
      // Never introduce a value/defaultValue on an uncontrolled native field.
      delete props.value;
      return React.createElement(node.tag, { ...props, ...controlProps, id: identity, ref: controlRef,
        'aria-describedby': node.attrs['aria-describedby'] || controlProps['aria-describedby'],
        'aria-invalid': error ? true : controlProps['aria-invalid'],
        'data-lq-component': kind === 'select' ? 'native-select' : kind,
        className: [node.attrs.class, className].filter(Boolean).join(' '),
      }, kind === 'select' ? children : undefined);
    }
    return React.createElement(node.tag, { ...props, ...(root ? { className: [node.attrs.class, fieldClassName].filter(Boolean).join(' ') } : {}) }, ...node.children.map(child => render(child)));
  }
  return render(tree, true);
}
export const LqNativeInput = React.forwardRef<HTMLInputElement, NativeFieldOptions & React.InputHTMLAttributes<HTMLInputElement>>(function LqNativeInput({ label, fieldClassName, help, error, controlSize, ...nativeProps }, ref) {
  return <LqField kind="input" {...{ label, fieldClassName, help, error, controlSize, nativeProps }} controlRef={ref} />;
});
export const LqNativeTextarea = React.forwardRef<HTMLTextAreaElement, NativeFieldOptions & React.TextareaHTMLAttributes<HTMLTextAreaElement>>(function LqNativeTextarea({ label, fieldClassName, help, error, controlSize, ...nativeProps }, ref) {
  return <LqField kind="textarea" {...{ label, fieldClassName, help, error, controlSize, nativeProps }} controlRef={ref} />;
});
export const LqNativeSelect = React.forwardRef<HTMLSelectElement, NativeFieldOptions & React.SelectHTMLAttributes<HTMLSelectElement>>(function LqNativeSelect({ label, fieldClassName, help, error, controlSize, ...nativeProps }, ref) {
  return <LqField kind="select" {...{ label, fieldClassName, help, error, controlSize, nativeProps }} controlRef={ref} />;
});

export function LqStatus({ family, state, label, className }: { family: string; state: string; label: string; className?: string }) {
  const tree = statusProps('status', { family, state, label }) as Exclude<PresentationTree, null | string | { icon: string }>;
  tree.attrs.class = [tree.attrs.class, className].filter(Boolean).join(' ');
  return renderTree(tree);
}
/** Adopt the native Card root for a domain composition that already owns its
 * semantic sections. In particular, list items keep their original list DOM. */
export function LqCardFrame({ as = 'article', title, attrs, className, children }: {
  as?: 'article' | 'li' | 'section'; title: string; attrs?: Attributes; className?: string; children: React.ReactNode;
}) {
  const nativeAttrs = Object.fromEntries(Object.entries(attrs || {}).map(([key, value]) => [key, typeof value === 'number' ? String(value) : value]));
  const tree = contentProps('card', { title, variant: 'default', attrs: nativeAttrs }) as Descriptor;
  return React.createElement(as, { ...reactAttributes(tree.attrs),
    className: [tree.attrs.class, className].filter(Boolean).join(' '), 'data-lq-component': 'surface',
  }, children);
}
/** Controlled filter choices keep native button/aria-pressed semantics. The
 * domain decides selection; this composition adds no keyboard/state owner. */
export function LqChipGroup<T extends string>({ label, value, items, onChange, className }: {
  label: string; value: T; items: { value: T; label: string; count?: number; disabled?: boolean }[];
  onChange: (value: T) => void; className?: string;
}) {
  return <div className={['lq-chip-group', className].filter(Boolean).join(' ')} role="group" aria-label={label}>
    {items.map(item => {
      const tree = componentTree('chip', { kind: 'filter', label: item.label, pressed: value === item.value, disabled: Boolean(item.disabled) });
      if (!tree || typeof tree === 'string' || 'icon' in tree) return null;
      return React.createElement(tree.tag, { ...reactAttributes(tree.attrs), key: item.value, 'data-lq-component': 'chip',
        'aria-label': item.count === undefined ? item.label : `${item.label} ${item.count}`,
        onClick: () => { if (!item.disabled) onChange(item.value); } },
        ...tree.children.map(child => renderTree(child)), item.count !== undefined ? <span className="lq-badge" aria-hidden="true">{item.count}</span> : null);
    })}
  </div>;
}
export function LqEmpty({ title, reason = 'empty', className, role }: { title: string; reason?: 'empty' | 'no-results'; className?: string; role?: 'status' }) {
  const tree = contentProps('empty', { title, reason, variant: 'inline' }) as Descriptor;
  function render(node: Descriptor | string | { slot: string }): React.ReactNode {
    if (typeof node === 'string') return node;
    if ('slot' in node) return null;
    return React.createElement(node.tag, { ...reactAttributes(node.attrs), ...(node === tree ? { className: [node.attrs.class, className].filter(Boolean).join(' '), role } : {}) }, ...node.children.map(render));
  }
  return render(tree);
}
export function LqPager({ label, page, pages, busy, onChange, className }: { label: string; page: number; pages: number; busy?: boolean; onChange: (page: number) => void; className?: string }) {
  return <nav className={['lq-pagination', className].filter(Boolean).join(' ')} aria-label={label}>
    <LqButton variant="soft" disabled={busy || page <= 0} onClick={() => onChange(page - 1)}>上一页</LqButton>
    <span>第 {page + 1} / {pages} 页</span>
    <LqButton variant="soft" disabled={busy || page + 1 >= pages} onClick={() => onChange(page + 1)}>下一页</LqButton>
  </nav>;
}

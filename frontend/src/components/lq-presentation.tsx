import * as React from 'react';
import { componentTree, type PresentationTree } from '../../../static/js/lq/components.js';
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

const reactName: Record<string, string> = { class: 'className', tabindex: 'tabIndex', 'stroke-width': 'strokeWidth',
  'stroke-linecap': 'strokeLinecap', 'stroke-linejoin': 'strokeLinejoin' };
const ownedRootProps = new Set(['children', 'dangerouslySetInnerHTML', 'href', 'type', 'disabled',
  'aria-label', 'aria-labelledby', 'aria-hidden', 'aria-live', 'aria-busy', 'aria-disabled', 'data-lq-disabled']);
function reactAttributes(attrs: Record<string, string>) {
  return Object.fromEntries(Object.entries(attrs).map(([key, value]) =>
    [reactName[key] || key, key === 'disabled' ? true : value]));
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
  className, onClick, onClickCapture, onKeyDown, onKeyDownCapture, iconContent, iconClassName, nativeProps, labelContent, ...options
}, ref) {
  const tree = componentTree('button', options);
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
    className: [tree.attrs.class, className].filter(Boolean).join(' '), ref,
    onClick: onClick || nativeProps?.onClick, onClickCapture: clickCapture,
    onKeyDown: onKeyDown || nativeProps?.onKeyDown, onKeyDownCapture: keyCapture,
  }, ...tree.children.map(child => renderTree(child, { iconContent, iconClassName, labelContent })));
});

export type LqAvatarProps = { name: string; src?: string; size?: 24 | 32 | 40 | 56; attrs?: Attributes; className?: string };
export const LqAvatar = React.forwardRef<HTMLSpanElement, LqAvatarProps>(function LqAvatar({ className, ...options }, ref) {
  const tree = componentTree('avatar', options);
  if (!tree || typeof tree === 'string' || 'icon' in tree) throw new TypeError('Invalid LQ avatar tree');
  return React.createElement('span', { ...reactAttributes(tree.attrs), ref,
    className: [tree.attrs.class, className].filter(Boolean).join(' '),
  }, ...tree.children.map(child => renderTree(child)));
});

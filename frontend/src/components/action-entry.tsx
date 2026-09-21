import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react';
import { LqAvatar, LqButton } from './lq-presentation';

function actionName(children: ReactNode, props: { 'aria-label'?: string; title?: string }) {
  return props['aria-label'] || (typeof children === 'string' ? children : '') || props.title || '';
}

type IconActionContentProps = {
  icon: ReactNode;
  iconClassName?: string;
  children?: ReactNode;
};

function IconActionContent({ icon, iconClassName, children }: IconActionContentProps) {
  return (
    <>
      <span className={iconClassName} aria-hidden="true">
        {icon}
      </span>
      {children}
    </>
  );
}

type IconActionLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & IconActionContentProps;

export function IconActionLink({
  icon,
  iconClassName,
  children,
  className,
  href,
  ...props
}: IconActionLinkProps) {
  if (href !== undefined) return <LqButton href={href} variant="ghost" ariaDisabled={props['aria-disabled'] === true || props['aria-disabled'] === 'true'}
    label={children == null ? '' : actionName(children, props)}
    icon="circle-question-mark" iconContent={icon} iconClassName={iconClassName} labelContent={children}
    attrs={{ 'aria-label': actionName(children, props), title: props.title, target: props.target, rel: props.rel }}
    className={className} nativeProps={props} />;
  return (
    <a className={className} {...props}>
      <IconActionContent icon={icon} iconClassName={iconClassName}>
        {children}
      </IconActionContent>
    </a>
  );
}

type IconActionButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & IconActionContentProps;

export function IconActionButton({
  icon,
  iconClassName,
  children,
  className,
  type = 'button',
  disabled,
  ...props
}: IconActionButtonProps) {
  return <LqButton type={type} disabled={disabled} variant="ghost" ariaDisabled={props['aria-disabled'] === true || props['aria-disabled'] === 'true'}
    label={children == null ? '' : actionName(children, props)}
    icon="circle-question-mark" iconContent={icon} iconClassName={iconClassName} labelContent={children}
    attrs={{ 'aria-label': actionName(children, props), title: props.title }} className={className} nativeProps={props} />;
}

type AvatarActionLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  avatarSrc?: string;
  avatarClassName?: string;
};

export function AvatarActionLink({
  avatarSrc = '/api/profile/avatar',
  avatarClassName,
  className,
  ...props
}: AvatarActionLinkProps) {
  return (
    <a className={['lq-avatar-link', className].filter(Boolean).join(' ')} {...props}>
      <LqAvatar className={avatarClassName} src={avatarSrc} name={actionName(null, props)} size={32} />
    </a>
  );
}

import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react';

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
  ...props
}: IconActionLinkProps) {
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
  ...props
}: IconActionButtonProps) {
  return (
    <button type={type} className={className} {...props}>
      <IconActionContent icon={icon} iconClassName={iconClassName}>
        {children}
      </IconActionContent>
    </button>
  );
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
    <a className={className} {...props}>
      <img className={avatarClassName} src={avatarSrc} alt="" loading="lazy" />
    </a>
  );
}

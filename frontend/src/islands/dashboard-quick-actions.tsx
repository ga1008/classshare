import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import { readIslandJsonPayload } from '@/lib/island-payload';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

type QuickAction = {
  mode: 'link' | 'button';
  label: string;
  description: string;
  href?: string;
  badge?: string | number | null;
  button_attrs?: Record<string, string>;
};

type QuickActionsPayload = {
  section: {
    title: string;
    subtitle: string;
  };
  actions: QuickAction[];
};

function toText(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback;
}

function normalizeQuickAction(value: unknown): QuickAction {
  const record = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
  const mode = record.mode === 'button' ? 'button' : 'link';
  const rawAttrs = record.button_attrs && typeof record.button_attrs === 'object'
    ? (record.button_attrs as Record<string, unknown>)
    : {};
  const button_attrs = Object.fromEntries(
    Object.entries(rawAttrs).map(([key, attrValue]) => [key, String(attrValue)]),
  );
  const badge = typeof record.badge === 'string' || typeof record.badge === 'number' ? record.badge : null;

  return {
    mode,
    label: toText(record.label, '入口'),
    description: toText(record.description),
    href: toText(record.href, '#'),
    badge,
    button_attrs,
  };
}

function normalizeQuickActionsPayload(value: unknown): QuickActionsPayload {
  const record = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
  const section = record.section && typeof record.section === 'object'
    ? (record.section as Record<string, unknown>)
    : {};

  return {
    section: {
      title: toText(section.title, '顺手入口'),
      subtitle: toText(section.subtitle),
    },
    actions: Array.isArray(record.actions) ? record.actions.map(normalizeQuickAction) : [],
  };
}

const quickActionClass = cn(
  'tw-flex tw-min-w-0 tw-gap-3 tw-rounded-lg tw-border tw-border-border tw-bg-card/80 tw-p-3.5',
  'tw-text-left tw-text-foreground tw-no-underline tw-transition-all tw-duration-150',
  'hover:tw--translate-y-px hover:tw-border-ring/40 hover:tw-bg-card hover:tw-shadow-soft-sm hover:tw-text-foreground hover:tw-no-underline',
  'focus-visible:tw-outline-none focus-visible:tw-ring-2 focus-visible:tw-ring-ring/60 focus-visible:tw-ring-offset-1',
);

function QuickActionContent({ action }: { action: QuickAction }) {
  return (
    <>
      <div className="tw-min-w-0 tw-flex-1">
        <strong className="tw-mb-1 tw-block tw-text-sm tw-font-semibold tw-text-foreground">{action.label}</strong>
        <p className="tw-m-0 tw-text-[0.85rem] tw-leading-relaxed tw-text-muted-foreground">{action.description}</p>
      </div>
      {action.badge ? (
        <Badge variant="secondary" className="tw-shrink-0 tw-self-start tw-rounded-full tw-bg-accent tw-px-2.5 tw-py-1 tw-text-accent-foreground">
          {action.badge}
        </Badge>
      ) : null}
    </>
  );
}

function DashboardQuickAction({ action }: { action: QuickAction }) {
  if (action.mode === 'link') {
    return (
      <a href={action.href || '#'} className={quickActionClass}>
        <QuickActionContent action={action} />
      </a>
    );
  }

  const buttonAttrs = action.button_attrs || {};

  return (
    <button type="button" className={cn(quickActionClass, 'tw-w-full tw-cursor-pointer')} {...buttonAttrs}>
      <QuickActionContent action={action} />
    </button>
  );
}

function DashboardQuickActions({ section, actions }: QuickActionsPayload) {
  return (
    <>
      <div className="dashboard-panel__header">
        <h2>{section.title}</h2>
        <p>{section.subtitle}</p>
      </div>
      <div className="dashboard-quick-actions">
        {actions.map((action, index) => (
          <DashboardQuickAction action={action} key={`${action.mode}-${action.href || action.label}-${index}`} />
        ))}
      </div>
    </>
  );
}

mountReactIslandsWhenReady({
  islandName: 'dashboard-quick-actions',
  getProps: (mountPoint) =>
    normalizeQuickActionsPayload(
      readIslandJsonPayload(mountPoint, '[data-dashboard-quick-actions-payload]'),
    ),
  render: (props) => <DashboardQuickActions {...props} />,
});

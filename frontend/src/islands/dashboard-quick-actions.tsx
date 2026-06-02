import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import { readIslandJsonPayload } from '@/lib/island-payload';

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

function QuickActionContent({ action }: { action: QuickAction }) {
  return (
    <>
      <div>
        <strong>{action.label}</strong>
        <p>{action.description}</p>
      </div>
      {action.badge ? <span className="dashboard-quick-action__badge">{action.badge}</span> : null}
    </>
  );
}

function DashboardQuickAction({ action }: { action: QuickAction }) {
  if (action.mode === 'link') {
    return (
      <a href={action.href || '#'} className="dashboard-quick-action">
        <QuickActionContent action={action} />
      </a>
    );
  }

  const buttonAttrs = action.button_attrs || {};

  return (
    <button
      type="button"
      className="dashboard-quick-action dashboard-quick-action--button"
      {...buttonAttrs}
    >
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

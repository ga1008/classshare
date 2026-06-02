import { useEffect } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

const APP_SHELL_ENTRY = 'app-shell';

function closeOtherTopbarMenus(currentMenu: HTMLDetailsElement, menus: HTMLDetailsElement[]) {
  for (const menu of menus) {
    if (menu !== currentMenu) {
      menu.open = false;
    }
  }
}

function syncSummaryState(menu: HTMLDetailsElement) {
  const summary = menu.querySelector<HTMLElement>('summary');
  if (summary) {
    summary.setAttribute('aria-expanded', menu.open ? 'true' : 'false');
  }
}

function useTopbarMenuEnhancements() {
  useEffect(() => {
    const menus = Array.from(document.querySelectorAll<HTMLDetailsElement>('details.app-topbar-menu'));
    if (menus.length === 0) {
      return undefined;
    }

    const handleToggle = (event: Event) => {
      const currentMenu = event.currentTarget as HTMLDetailsElement;
      syncSummaryState(currentMenu);
      if (currentMenu.open) {
        closeOtherTopbarMenus(currentMenu, menus);
      }
    };

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (!target) {
        return;
      }
      const isInsideMenu = menus.some((menu) => menu.contains(target));
      if (!isInsideMenu) {
        for (const menu of menus) {
          menu.open = false;
        }
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return;
      }
      const activeMenu = menus.find((menu) => menu.open);
      if (!activeMenu) {
        return;
      }
      activeMenu.open = false;
      activeMenu.querySelector<HTMLElement>('summary')?.focus();
    };

    for (const menu of menus) {
      syncSummaryState(menu);
      menu.addEventListener('toggle', handleToggle);
    }
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      for (const menu of menus) {
        menu.removeEventListener('toggle', handleToggle);
      }
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);
}

function AppShellIsland() {
  useTopbarMenuEnhancements();

  return null;
}

mountReactIslandsWhenReady({
  islandName: APP_SHELL_ENTRY,
  defaultMountIdPrefix: APP_SHELL_ENTRY,
  getProps: () => ({}),
  render: () => <AppShellIsland />,
});

import { useEffect } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import { legacyModuleUrl } from '@/lib/static-assets';

const LEGACY_MATERIALS_MANAGE_CONTROLLER_URL = legacyModuleUrl('materials_manage.js');

function MaterialsManagePageController() {
  useEffect(() => {
    const app = document.querySelector<HTMLElement>('[data-materials-manage-page-app]');
    if (!app || app.dataset.materialsManageControllerMounted === 'true') {
      return;
    }

    app.dataset.materialsManageControllerMounted = 'true';
    window.__LANSHARE_MATERIALS_MANAGE_PAGE_CONTROLLER__ ||= import(
      /* @vite-ignore */ LEGACY_MATERIALS_MANAGE_CONTROLLER_URL
    );
    window.__LANSHARE_MATERIALS_MANAGE_PAGE_CONTROLLER__.catch((error: unknown) => {
      app.dataset.materialsManageControllerMounted = 'false';
      console.error('[materials-manage-page] controller failed to load', error);
    });
  }, []);

  return null;
}

mountReactIslandsWhenReady({
  islandName: 'materials-manage-page',
  defaultMountIdPrefix: 'materials-manage-page',
  render: () => <MaterialsManagePageController />,
  getProps: () => ({}),
});

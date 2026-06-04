import { useEffect } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

const LEGACY_MESSAGE_CENTER_CONTROLLER_URL = '/static/js/message_center.js?v=p12-page-controller-20260604';

function loadMessageCenterController() {
  if (window.__LANSHARE_MESSAGE_CENTER_PAGE_CONTROLLER__) {
    return window.__LANSHARE_MESSAGE_CENTER_PAGE_CONTROLLER__;
  }

  const controllerPromise = import(
    /* @vite-ignore */ LEGACY_MESSAGE_CENTER_CONTROLLER_URL
  );
  window.__LANSHARE_MESSAGE_CENTER_PAGE_CONTROLLER__ = controllerPromise;
  return controllerPromise;
}

function MessageCenterPageController() {
  useEffect(() => {
    const app = document.querySelector<HTMLElement>('[data-message-center-app]');
    if (!app || app.dataset.messageCenterControllerMounted === 'true') {
      return;
    }

    let cancelled = false;
    app.dataset.messageCenterControllerMounted = 'true';

    loadMessageCenterController().catch((error: unknown) => {
      if (cancelled) {
        return;
      }
      app.dataset.messageCenterControllerMounted = 'false';
      console.error('[message-center-page] controller failed to load', error);
      window.UI?.showToast?.('信息中心初始化失败，请刷新重试。', 'error');
    });

    return () => {
      cancelled = true;
    };
  }, []);

  return null;
}

mountReactIslandsWhenReady({
  islandName: 'message-center-page',
  defaultMountIdPrefix: 'message-center-page',
  render: () => <MessageCenterPageController />,
  getProps: () => ({}),
});

import { useEffect } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

const LEGACY_MODULES = {
  ui: '/static/js/ui.js',
  chat: '/static/js/chat.js?v=p12-classroom-page-20260604',
  privateMessages: '/static/js/classroom_private_messages.js?v=p12-classroom-page-20260604',
  files: '/static/js/app_files.js',
  materials: '/static/js/classroom_materials.js?v=p12-classroom-page-20260604',
  exams: '/static/js/app_exams.js',
  classroomPage: '/static/js/classroom_page.js?v=p12-classroom-page-20260607-campus',
  learningProgress: '/static/js/learning_progress.js?v=cultivation-certificate-20260612',
  interactions: '/static/js/classroom_interactions.js?v=activity-dock-declutter-20260606',
  collaboration: '/static/js/collaboration.js?v=p12-classroom-page-20260604',
} as const;

type LegacyModule = Record<string, unknown>;

type ClassroomChatConstructor = new (options: Record<string, unknown>) => {
  init: () => void;
  scheduleDiscussionRoomResize: () => void;
  onFileEvent?: () => void;
};

type ClassroomPrivateMessagesConstructor = new (options: Record<string, unknown>) => {
  init: () => void;
};

function loadLegacyModule(url: string): Promise<LegacyModule> {
  return import(/* @vite-ignore */ url) as Promise<LegacyModule>;
}

function resolveFunction(module: LegacyModule, name: string): (...args: unknown[]) => unknown {
  const value = module[name];
  if (typeof value !== 'function') {
    throw new Error(`Missing legacy classroom export: ${name}`);
  }
  return value as (...args: unknown[]) => unknown;
}

function resolveConstructor<T>(module: LegacyModule, name: string): T {
  const value = module[name];
  if (typeof value !== 'function') {
    throw new Error(`Missing legacy classroom constructor: ${name}`);
  }
  return value as T;
}

async function bootstrapClassroomPage(app: HTMLElement) {
  if (app.dataset.classroomPageControllerMounted === 'true') {
    return;
  }
  app.dataset.classroomPageControllerMounted = 'true';

  const [
    ui,
    chatModule,
    privateMessagesModule,
    fileApp,
    materialsApp,
    examApp,
    classroomPageModule,
    learningProgressModule,
    interactionsModule,
    collaborationModule,
  ] = await Promise.all([
    loadLegacyModule(LEGACY_MODULES.ui),
    loadLegacyModule(LEGACY_MODULES.chat),
    loadLegacyModule(LEGACY_MODULES.privateMessages),
    loadLegacyModule(LEGACY_MODULES.files),
    loadLegacyModule(LEGACY_MODULES.materials),
    loadLegacyModule(LEGACY_MODULES.exams),
    loadLegacyModule(LEGACY_MODULES.classroomPage),
    loadLegacyModule(LEGACY_MODULES.learningProgress),
    loadLegacyModule(LEGACY_MODULES.interactions),
    loadLegacyModule(LEGACY_MODULES.collaboration),
  ]);

  window.UI = ui as Window['UI'];
  window.fileApp = fileApp as Window['fileApp'];
  window.materialsApp = materialsApp as Window['materialsApp'];
  window.examApp = examApp as Window['examApp'];

  const appConfig = window.APP_CONFIG || {};
  const classOfferingId = appConfig.classOfferingId;

  const BehaviorTracker = window.BehaviorTracker;
  if (typeof BehaviorTracker === 'function') {
    window.behaviorTracker = new BehaviorTracker({
      classOfferingId,
      pageKey: 'classroom_discussion',
    }).start();
  }

  resolveFunction(classroomPageModule, 'initClassroomPage')();
  resolveFunction(learningProgressModule, 'initLearningProgress')(appConfig);
  resolveFunction(interactionsModule, 'initClassroomInteractions')(appConfig);
  resolveFunction(collaborationModule, 'initCollaborationPanel')(appConfig);

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const assignmentEntry = target.closest<HTMLElement>('a[href^="/assignment/"], [data-assignment-link]');
    const href = assignmentEntry?.getAttribute('href') || assignmentEntry?.dataset.assignmentLink;
    if (href) {
      window.behaviorTracker?.logClick?.(
        '点击作业入口',
        { href },
        'classroom_discussion',
      );
    }
  });

  const ClassroomChat = resolveConstructor<ClassroomChatConstructor>(chatModule, 'ClassroomChat');
  const chatApp = new ClassroomChat({
    classOfferingId,
    chatMessagesContainerId: 'chat-messages',
    chatInputId: 'chat-input',
    chatFormId: 'chat-form',
    emojiTriggerButtonId: 'chat-emoji-trigger-btn',
    emojiPopoverId: 'chat-emoji-popover',
    emojiCloseButtonId: 'chat-emoji-close-btn',
    emojiFrequentRowId: 'chat-emoji-frequent-row',
    emojiCategoriesId: 'chat-emoji-categories',
    customEmojiGridId: 'chat-custom-emoji-grid',
    customEmojiUploadButtonId: 'chat-custom-emoji-upload-btn',
    customEmojiFileInputId: 'chat-custom-emoji-file-input',
    customEmojiUploadStatusId: 'chat-custom-emoji-upload-status',
    customEmojiProgressId: 'chat-custom-emoji-progress',
    customEmojiProgressBarId: 'chat-custom-emoji-progress-bar',
    emojiPreviewRowId: 'chat-emoji-preview-row',
    emojiSetNoteId: 'chat-emoji-set-note',
    composerExpandButtonId: 'chat-composer-expand-btn',
    attachmentTriggerButtonId: 'chat-attachment-trigger-btn',
    attachmentFileInputId: 'chat-attachment-file-input',
    attachmentPreviewRowId: 'chat-attachment-preview-row',
    quotePreviewId: 'chat-quote-preview',
    messageMenuId: 'chat-message-menu',
    displayNameId: 'chat-display-name',
    aliasMetaId: 'chat-alias-meta',
    discussionMoodHeadlineId: 'discussion-mood-headline',
    discussionMoodDetailId: 'discussion-mood-detail',
    switchAliasButtonId: 'chat-switch-alias-btn',
    mentionAllButtonId: 'chat-mention-all-btn',
    historyLoaderId: 'chat-history-loader',
    historyLoadButtonId: 'chat-history-load-btn',
    statusIndicatorId: 'ws-status',
    statusTextId: 'ws-status-text',
    onlineCountId: 'ws-online-count',
    discussionRoomId: 'discussion-room',
    workspaceContentId: 'workspace-content',
    currentUser: appConfig.userInfo,
  });
  chatApp.init();
  chatApp.onFileEvent = () => {
    const refreshFiles = window.fileApp?.refreshFiles;
    if (typeof refreshFiles === 'function') {
      refreshFiles();
    }
  };

  const ClassroomPrivateMessages = resolveConstructor<ClassroomPrivateMessagesConstructor>(
    privateMessagesModule,
    'ClassroomPrivateMessages',
  );
  const privateMessagesApp = new ClassroomPrivateMessages({
    classOfferingId,
    rootId: 'discussion-room',
    broadcastBodyId: 'discussion-broadcast-body',
    broadcastComposerId: 'discussion-broadcast-composer',
    privateBodyId: 'classroom-private-body',
    privateComposerId: 'classroom-private-composer',
    tabSelector: '[data-classroom-message-tab]',
    contactSelectId: 'classroom-private-contact-select',
    contactInputId: 'classroom-private-contact-input',
    contactListId: 'classroom-private-contact-list',
    contactToggleId: 'classroom-private-contact-toggle',
    statusId: 'classroom-private-status',
    conversationId: 'classroom-private-conversation',
    formId: 'classroom-private-form',
    inputId: 'classroom-private-input',
    dropzoneId: 'classroom-private-dropzone',
    imageButtonId: 'classroom-private-image-btn',
    fileButtonId: 'classroom-private-file-btn',
    imageInputId: 'classroom-private-image-input',
    fileInputId: 'classroom-private-file-input',
    previewId: 'classroom-private-attachment-preview',
    sendButtonId: 'classroom-private-send-btn',
    onModeChange: () => chatApp.scheduleDiscussionRoomResize(),
  });
  privateMessagesApp.init();

  resolveFunction(fileApp, 'init')(appConfig);
  resolveFunction(materialsApp, 'init')(appConfig);
  resolveFunction(examApp, 'init')(appConfig);
}

function ClassroomPageController() {
  useEffect(() => {
    const app = document.querySelector<HTMLElement>('[data-classroom-page-app]');
    if (!app) {
      return;
    }

    bootstrapClassroomPage(app).catch((error: unknown) => {
      app.dataset.classroomPageControllerMounted = 'false';
      console.error('[classroom-page] controller failed to load', error);
      window.UI?.showToast?.('课堂页面初始化失败，请刷新重试。', 'error');
    });
  }, []);

  return null;
}

mountReactIslandsWhenReady({
  islandName: 'classroom-page',
  defaultMountIdPrefix: 'classroom-page',
  render: () => <ClassroomPageController />,
  getProps: () => ({}),
});

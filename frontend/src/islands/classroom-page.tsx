import { useEffect } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import { classroomReadiness } from '@/lib/classroom-bootstrap-ready';
import { legacyModuleUrl } from '@/lib/static-assets';
import { ClassroomWorkspace } from './classroom-workspace';

const LEGACY_MODULES = {
  ui: legacyModuleUrl('ui.js'),
  chat: legacyModuleUrl('chat.js'),
  privateMessages: legacyModuleUrl('classroom_private_messages.js'),
  files: legacyModuleUrl('app_files.js'),
  materials: legacyModuleUrl('classroom_materials.js'),
  exams: legacyModuleUrl('app_exams.js'),
  classroomPage: legacyModuleUrl('classroom_page.js'),
  learningProgress: legacyModuleUrl('learning_progress.js'),
  interactions: legacyModuleUrl('classroom_interactions.js'),
  collaboration: legacyModuleUrl('collaboration.js'),
  polls: legacyModuleUrl('classroom_polls.js'),
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

async function initializeClassroomPage(app: HTMLElement) {
  // These editors live in secondary surfaces. Start their downloads after the
  // document's first render instead of preloading them alongside critical CSS.
  // Each island reads its latest snapshot when it mounts; no commands are lost.
  const secondaryIslands = Promise.all([
    import('./assignment-authoring-sync'),
    import('./exam-assign-sync'),
  ]);
  void secondaryIslands.catch(error => {
    console.error('[classroom-page] secondary tools failed to load', error);
    window.UI?.showToast?.('课堂编辑工具加载失败，请刷新重试。', 'error');
  });

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
    pollsModule,
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
    loadLegacyModule(LEGACY_MODULES.polls),
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
  resolveFunction(pollsModule, 'initClassroomPolls')(appConfig);

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
    workspaceContentId: 'cw-primary-content',
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
  await secondaryIslands;
}

function bootstrapClassroomPage(app: HTMLElement) {
  return classroomReadiness.start(async () => {
    app.dataset.classroomPageControllerMounted = 'true';
    try {
      await initializeClassroomPage(app);
    } catch (error) {
      app.dataset.classroomPageControllerMounted = 'false';
      console.error('[classroom-page] controller failed to load', error);
      window.UI?.showToast?.('课堂页面初始化失败，请刷新重试。', 'error');
      throw error;
    }
  });
}

export function ClassroomPageController() {
  useEffect(() => {
    const app = document.querySelector<HTMLElement>('[data-classroom-page-app]');
    if (!app) {
      return;
    }

    // Unmounting this React view does not stop the document's native controllers.
    // start() owns readiness and consumes failure even if no view remains.
    void bootstrapClassroomPage(app);
  }, []);

  return <ClassroomWorkspace />;
}

mountReactIslandsWhenReady({
  islandName: 'classroom-page',
  defaultMountIdPrefix: 'classroom-page',
  render: () => <ClassroomPageController />,
  getProps: () => ({}),
});

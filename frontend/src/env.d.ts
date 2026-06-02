/// <reference types="vite/client" />

interface Window {
  APP_CONFIG?: unknown;
  __LANSHARE_REACT_ISLANDS__?: {
    version: string;
    mounted: string[];
  };
  refreshMessageCenterBell?: (options?: { allowPopup?: boolean }) => void | Promise<void>;
  refreshBlogTopbar?: () => void | Promise<void>;
  refreshSubmissions?: () => void | Promise<void>;
  setFilter?: (filter: string, button?: HTMLElement | null) => void;
  aiGradeAll?: () => void | Promise<void>;
  zeroUnsubmittedScores?: () => void | Promise<void>;
  openWithdrawModalForSelected?: () => void;
  __LANSHARE_TEACHER_SUBMISSION_WORKBENCH__?: unknown;
  __LANSHARE_MESSAGE_CENTER_WORKSPACE__?: unknown;
  __LANSHARE_CLASSROOM_WORKSPACE_NAV__?: unknown;
  __LANSHARE_ASSIGNMENT_TASK_BOARD__?: unknown;
  __LANSHARE_CLASSROOM_ACTIVITY_WORKSPACE__?: unknown;
  __LANSHARE_RESOURCE_WORKSPACE__?: unknown;
  __LANSHARE_MATERIAL_LEARNING_PATH__?: unknown;
  __LANSHARE_LEARNING_PROGRESS_COMMANDS_BOUND__?: boolean;
  __LANSHARE_ASSIGNMENT_AUTHORING__?: unknown;
  __LANSHARE_EXAM_ASSIGN__?: unknown;
  __LANSHARE_ASSIGNMENT_UPLOAD_SNAPSHOT__?: {
    count?: number;
    totalBytes?: number;
    entries?: unknown[];
  };
  showMessage?: (message: string, type?: string, duration?: number) => void;
  UI?: {
    showToast?: (message: string, type?: string, duration?: number) => void;
    openModal?: (modalId: string) => void;
    closeModal?: (modalId: string) => void;
  };
}

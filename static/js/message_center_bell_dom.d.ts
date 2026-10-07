export type MessageBellTargets = {
  bell: HTMLElement | null;
  count: HTMLElement | null;
  caption: HTMLElement | null;
};
export type MessageBellTargetResolver = (shell: HTMLElement) => MessageBellTargets;
export function createMessageBellTargets(): MessageBellTargetResolver;

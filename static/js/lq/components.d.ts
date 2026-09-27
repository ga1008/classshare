export type PresentationTree = null | string | { icon: string } | {
  tag: string; attrs: Record<string, string>; children: PresentationTree[];
};
export function componentTree(kind: string, props?: Record<string, unknown>): PresentationTree;
export function createComponent(kind: string, props?: Record<string, unknown>, doc?: Document): HTMLElement;

export type PresentationTree = null | string | { icon: string } | {
  tag: string; attrs: Record<string, string>; children: PresentationTree[];
};
export function componentTree(kind: string, props?: Record<string, unknown>): PresentationTree;

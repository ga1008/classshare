export type IslandPayloadMountPoint = Pick<HTMLElement, 'querySelector'>;

export function readIslandJsonPayload(mountPoint: IslandPayloadMountPoint, selector: string): unknown {
  const payloadNode = mountPoint.querySelector<HTMLScriptElement>(selector);
  if (!payloadNode) {
    throw new Error(`Missing island payload: ${selector}`);
  }

  const rawPayload = payloadNode.textContent || '{}';
  return JSON.parse(rawPayload) as unknown;
}

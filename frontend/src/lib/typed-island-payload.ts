import type { ZodType } from 'zod';

import { readIslandJsonPayload, type IslandPayloadMountPoint } from './island-payload';

export function readIslandPayload<T>(
  mountPoint: IslandPayloadMountPoint,
  selector: string,
  schema: ZodType<T>,
): T {
  return schema.parse(readIslandJsonPayload(mountPoint, selector));
}

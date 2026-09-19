export type ScheduleRoutePoint = { x: number; y: number };
export type ScheduleRouteObstacle = { key: string; left: number; top: number; right: number; bottom: number };
export type ScheduleRouteConnection = {
  key: string;
  sourceKey?: string | null;
  targetKey?: string | null;
  edge?: 'left' | 'right' | null;
  direction?: 'local' | 'outgoing' | 'incoming';
  label: string;
  color?: string;
  [key: string]: unknown;
};
export type ScheduleChangeRoute = ScheduleRouteConnection & {
  points: ScheduleRoutePoint[];
  reason: 'invalid_canvas' | 'missing_endpoint' | 'same_time' | 'endpoint_occluded' | 'no_safe_path' | null;
  /** True only when prior geometry remains attached and safe after remeasurement. */
  reused: boolean;
  /** Centre and axis-aligned box; vertical labels rotate their text -90deg. */
  labelPlacement: { x: number; y: number; vertical: boolean; width: number; height: number } | null;
};
export function routeScheduleChanges(options: {
  width: number; height: number;
  obstacles: ScheduleRouteObstacle[];
  connections: ScheduleRouteConnection[];
  previousRoutes?: ScheduleChangeRoute[];
}): ScheduleChangeRoute[];
export function roundedScheduleRoute(points: ScheduleRoutePoint[], obstacles?: ScheduleRouteObstacle[], radius?: number): string;

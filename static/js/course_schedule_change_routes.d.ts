export type ScheduleRoutePoint = { x: number; y: number };
export type ScheduleRouteObstacle = { key: string; left: number; top: number; right: number; bottom: number };
export type ScheduleRouteConnection = {
  key: string;
  sourceKey?: string | null;
  targetKey?: string | null;
  edge?: 'left' | 'right';
  direction?: 'outgoing' | 'incoming';
  label: string;
  color?: string;
  [key: string]: unknown;
};
export type ScheduleChangeRoute = ScheduleRouteConnection & {
  points: ScheduleRoutePoint[];
  /** Centre and axis-aligned box; vertical labels rotate their text -90deg. */
  labelPlacement: { x: number; y: number; vertical: boolean; width: number; height: number } | null;
};
export function routeScheduleChanges(options: {
  width: number; height: number;
  obstacles: ScheduleRouteObstacle[];
  connections: ScheduleRouteConnection[];
}): ScheduleChangeRoute[];

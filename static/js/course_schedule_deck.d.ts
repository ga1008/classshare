export function scheduleWheelIntent(options: {
  deltaY?: number; deltaMode?: number; ctrlKey?: boolean; metaKey?: boolean;
  index?: number; length?: number; pending?: number;
}): { consume: boolean; step: number; pending: number };

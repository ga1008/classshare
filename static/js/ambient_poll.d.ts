export type AmbientPoll = {
  refresh(): Promise<void>;
  start(initialDelayMs?: number): void;
  dispose(): void;
  readonly inFlight: boolean;
};

export function createAmbientPoll(
  task: (signal: AbortSignal) => Promise<void>,
  options: { intervalMs: number; timeoutMs?: number; active?: () => boolean },
): AmbientPoll;

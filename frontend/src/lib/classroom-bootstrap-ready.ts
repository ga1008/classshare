/** A click may arrive before the legacy controllers finish downloading.
 * Keep that action pending instead of forwarding it to an unbound control. */
export function createClassroomReadiness() {
  type Result = { ok: true } | { ok: false; error: unknown };
  let settle!: (result: Result) => void;
  const result = new Promise<Result>(resolve => {
    settle = resolve;
  });
  let bootstrap: Promise<void> | undefined;
  return {
    complete: (...failure: [] | [unknown]) => {
      settle(failure.length ? { ok: false, error: failure[0] } : { ok: true });
    },
    // The native controllers belong to the document, not to a React effect.
    // Keep the same pending/success/failed bootstrap through StrictMode and
    // remounts: retrying a partial failure could duplicate listeners or sockets.
    start: (initialize: () => void | Promise<void>): Promise<void> => {
      if (!bootstrap) {
        bootstrap = Promise.resolve().then(initialize);
        void bootstrap.then(
          () => settle({ ok: true }),
          error => settle({ ok: false, error }),
        );
      }
      return bootstrap;
    },
    wait: async () => {
      const outcome = await result;
      if (!outcome.ok) throw outcome.error;
    },
  };
}

export const classroomReadiness = createClassroomReadiness();

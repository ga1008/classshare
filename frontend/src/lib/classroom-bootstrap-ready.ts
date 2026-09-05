/** A click may arrive before the legacy controllers finish downloading.
 * Keep that action pending instead of forwarding it to an unbound control. */
export function createClassroomReadiness() {
  let complete!: (error?: unknown) => void;
  const result = new Promise<{ error?: unknown }>(resolve => {
    complete = error => resolve({ error });
  });
  return {
    complete,
    wait: async () => {
      const { error } = await result;
      if (error) throw error;
    },
  };
}

export const classroomReadiness = createClassroomReadiness();

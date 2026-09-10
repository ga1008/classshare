// One image-owned provider shares this gate across ALL workflow instances.
// This is a runtime lifecycle limit, not isolation from escaped workflow code.
export class AdmissionGate {
  #used = 0;
  #active = false;
  #closed = false;
  #queue = [];
  constructor(limit = 4) {
    if (limit !== 4) throw new Error('Workflow admission limit is fixed at four');
  }
  get snapshot() { return { used: this.#used, active: Number(this.#active), queued: this.#queue.length, closed: this.#closed }; }
  async acquire(signal) {
    signal.throwIfAborted();
    if (this.#closed || this.#used >= 4) throw new Error('Task workflow child admission exhausted or closed');
    this.#used++;
    if (this.#active) await new Promise((resolve, reject) => {
      const item = { resolve, reject, signal, abort: undefined };
      item.abort = () => {
        const index = this.#queue.indexOf(item);
        if (index >= 0) this.#queue.splice(index, 1);
        reject(new Error('Queued workflow child canceled'));
      };
      this.#queue.push(item);
      signal.addEventListener('abort', item.abort, { once: true });
      if (signal.aborted) item.abort();
    });
    else this.#active = true;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.#active = false;
      while (this.#queue.length) {
        const item = this.#queue.shift();
        item.signal.removeEventListener('abort', item.abort);
        if (item.signal.aborted) { item.reject(new Error('Queued workflow child canceled')); continue; }
        this.#active = true;
        item.resolve();
        break;
      }
    };
  }
  close() {
    this.#closed = true;
    for (const item of this.#queue.splice(0)) {
      item.signal.removeEventListener('abort', item.abort);
      item.reject(new Error('Workflow provider closed'));
    }
  }
}

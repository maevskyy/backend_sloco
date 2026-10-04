/**
 * Counting semaphore with a bounded wait: `acquire` resolves `true` once a slot
 * is free, or `false` when none freed up within `waitMs` (the caller then
 * answers "busy" instead of queueing without end). Waiters are served FIFO.
 */
export class Semaphore {
  private active = 0;
  private readonly waiters: Array<() => void> = [];

  constructor(private readonly limit: number) {}

  get inUse() {
    return this.active;
  }

  get waiting() {
    return this.waiters.length;
  }

  acquire(waitMs: number): Promise<boolean> {
    if (this.active < this.limit) {
      this.active += 1;
      return Promise.resolve(true);
    }

    if (waitMs <= 0) {
      return Promise.resolve(false);
    }

    return new Promise((resolve) => {
      const waiter = () => {
        clearTimeout(timer);
        this.active += 1;
        resolve(true);
      };
      const timer = setTimeout(() => {
        const index = this.waiters.indexOf(waiter);
        if (index >= 0) this.waiters.splice(index, 1);
        resolve(false);
      }, waitMs);

      this.waiters.push(waiter);
    });
  }

  release() {
    this.active -= 1;
    this.waiters.shift()?.();
  }
}

import { signal } from '@angular/core';

/** One manual ghost click, with the idempotency key it is sent with. */
export interface GhostClick {
  readonly seq: number;
  readonly matchId: string;
  readonly side: 'meron' | 'wala';
  readonly amount: number;
  readonly label: string;
  readonly requestId: string;
}

export type GhostOutcome = 'accepted' | 'rejected' | 'cancelled';

export interface GhostQueueHooks {
  /** Send one click. Resolves when the server accepted it; rejects when the server refused it. */
  send(click: GhostClick): Promise<void>;
  /** Every click ends exactly once: accepted or rejected by the server, or cancelled before it was sent. */
  settled(click: GhostClick, outcome: GhostOutcome, error?: unknown): void;
}

/**
 * Manual ghost clicks, sent in click order with at most `maxInFlight` requests at a time.
 *
 * Every write on a fight takes the event lock on the server, so they run one at a time there. Eight
 * parallel clicks therefore queued ahead of Stop and Close. Bounding the requests in flight bounds how
 * long a Stop or Close waits. `halt()` serves them first: nothing more is sent, unsent clicks are
 * cancelled and reported (never silently dropped), and in-flight requests finish and are decided by the
 * server, which stays authoritative and refuses any click that arrives after betting closed.
 */
export class GhostQueue {
  private readonly waiting: GhostClick[] = [];
  private readonly sending = new Set<number>();
  private seq = 0;
  private halted = false;
  private idle: (() => void)[] = [];
  readonly queued = signal(0);
  readonly inFlight = signal(0);
  readonly stopped = signal(false);

  constructor(
    private readonly hooks: GhostQueueHooks,
    readonly maxInFlight = 2,
    readonly capacity = 64,
  ) {}

  /** The number of clicks not yet decided by the server. */
  pending(): number {
    return this.waiting.length + this.sending.size;
  }

  canAccept(): boolean {
    return !this.halted && this.pending() < this.capacity;
  }

  /** Queue a click. Returns it, or null when the queue is halted or full (the button is disabled then). */
  enqueue(click: Omit<GhostClick, 'seq'>): GhostClick | null {
    if (!this.canAccept()) return null;
    const queued: GhostClick = { ...click, seq: ++this.seq };
    this.waiting.push(queued);
    this.publish();
    this.pump();
    return queued;
  }

  /** Stop/Close first: send nothing more and cancel every unsent click. Returns the cancelled clicks. */
  halt(): GhostClick[] {
    this.halted = true;
    this.stopped.set(true);
    const cancelled = this.waiting.splice(0);
    this.publish();
    for (const click of cancelled) this.hooks.settled(click, 'cancelled');
    this.notifyIdle();
    return cancelled;
  }

  /** Accept and send clicks again (after Stop finished while betting is still open). */
  resume(): void {
    this.halted = false;
    this.stopped.set(false);
    this.pump();
  }

  /** Resolves once no click is waiting or in flight. */
  drained(): Promise<void> {
    return this.pending() === 0 ? Promise.resolve() : new Promise((resolve) => this.idle.push(resolve));
  }

  private pump(): void {
    while (!this.halted && this.sending.size < this.maxInFlight && this.waiting.length) {
      const click = this.waiting.shift()!;
      this.sending.add(click.seq);
      this.publish();
      void this.hooks
        .send(click)
        .then(
          () => this.hooks.settled(click, 'accepted'),
          (error: unknown) => this.hooks.settled(click, 'rejected', error),
        )
        .finally(() => {
          this.sending.delete(click.seq);
          this.publish();
          this.notifyIdle();
          this.pump();
        });
    }
  }

  private publish(): void {
    this.queued.set(this.waiting.length);
    this.inFlight.set(this.sending.size);
  }

  private notifyIdle(): void {
    if (this.pending() > 0) return;
    const waiting = this.idle.splice(0);
    for (const resolve of waiting) resolve();
  }
}

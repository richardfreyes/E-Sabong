import { ChangeDetectionStrategy, Component, DestroyRef, effect, inject, input, signal, untracked } from '@angular/core';

import { formatArenaPoints, toCents } from '@shared/pipes';

/** Length of one count, in milliseconds. Each new pool figure (e.g. a dummy +50K) counts for 1.5 s. */
export const COUNT_UP_MS = 1500;

/**
 * A play-balance figure that counts up to its new value (1, 2, 3 … 100) instead of jumping, so a
 * pool visibly grows as players add to it. A new value mid-count restarts the count from the figure on screen. Display only: the value is always the server's figure.
 * It snaps without counting on the first value, when a value drops (a new fight starts at zero),
 * when the value is unavailable (a dash) and when the user prefers reduced motion.
 */
@Component({
  selector: 'pp-count-up',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `{{ text() }}`,
})
export class CountUpComponent {
  /** Decimal string from the API ("1234.50"); null shows a dash. */
  readonly value = input<string | null>(null);
  readonly text = signal('—');
  /** Hundredths currently on screen; null while a dash is shown. */
  private shown: number | null = null;
  private frame = 0;

  constructor() {
    effect(() => {
      const target = toCents(this.value() ?? '');
      untracked(() => this.countTo(target));
    });
    inject(DestroyRef).onDestroy(() => this.stop());
  }

  private countTo(target: number | null): void {
    this.stop();
    const from = this.shown;
    if (target === null || from === null || target <= from || !canAnimate()) {
      this.show(target);
      return;
    }
    const start = performance.now();
    // One clock for start and every frame: a frame's own timestamp can predate `start`.
    const step = () => {
      const t = Math.min(1, Math.max(0, (performance.now() - start) / COUNT_UP_MS));
      // Steady pace, so the figure visibly counts the whole 1.5 s instead of jumping near the start.
      this.show(t === 1 ? target : Math.round(from + (target - from) * t));
      if (t < 1) this.frame = requestAnimationFrame(step);
    };
    this.frame = requestAnimationFrame(step);
  }

  private show(cents: number | null): void {
    this.shown = cents;
    this.text.set(cents === null ? '—' : formatArenaPoints(cents));
  }

  private stop(): void {
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
  }
}

function canAnimate(): boolean {
  if (typeof requestAnimationFrame !== 'function') return false;
  return !globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

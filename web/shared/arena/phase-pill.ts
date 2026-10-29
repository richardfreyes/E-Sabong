import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { Phase } from './match-phase';

/** Upcoming / Live / Finished / Closed / Cancelled. Live carries the lamplight dot. */
@Component({
  selector: 'pp-phase-pill',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: inline-flex; }
    span { display: inline-flex; align-items: center; gap: 0.4rem; border-radius: 999px; padding: 0.2rem 0.65rem;
      font-size: 0.75rem; font-weight: 600; line-height: 1.1rem; border: 1px solid var(--pp-rope); color: var(--pp-ash); white-space: nowrap; }
    .live { color: var(--pp-lamp); border-color: color-mix(in srgb, var(--pp-lamp) 45%, transparent);
      background: color-mix(in srgb, var(--pp-lamp) 10%, transparent); }
    .upcoming { color: var(--pp-chalk); }
    .finished, .closed { color: var(--pp-ash); }
    .cancelled { color: var(--pp-void); text-decoration: line-through; text-decoration-thickness: 1px; }
  `,
  template: `<span [class]="phase().tone">@if (phase().tone === 'live') {<i class="pp-live-dot" aria-hidden="true"></i>}{{ phase().label }}</span>`,
})
export class PhasePillComponent {
  readonly phase = input.required<Phase>();
}

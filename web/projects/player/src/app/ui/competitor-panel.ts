import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import type { Match } from '@shared/models';

/** The two public match sides; custom entry names stay out of the player UI. */
@Component({
  selector: 'pp-competitor-panel',
  imports: [MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; min-width: 0; }
    article { position: relative; display: grid; place-items: center; min-height: 108px; padding: 1rem;
      border-radius: 0.75rem; border: 1px solid color-mix(in srgb, var(--side) 65%, var(--pp-rope));
      background: color-mix(in srgb, var(--side) 14%, var(--pp-pit)); color: var(--side-text); transition: opacity 200ms ease; }
    .name { font-size: clamp(1.75rem, 3vw, 2.6rem); }
    .meron { --side: var(--pp-meron); --side-text: var(--meron-text); }
    .wala { --side: var(--pp-wala); --side-text: var(--wala-text); }
    .winner { border-color: var(--side); background: color-mix(in srgb, var(--side) 78%, var(--pp-pit)); color: white; }
    .lost { opacity: 0.55; }
    mat-icon { position: absolute; right: 0.6rem; top: 0.6rem; width: 18px; height: 18px; font-size: 18px; }
  `,
  template: `
    <article [class]="side()" [class.winner]="won()" [class.lost]="lost()" [attr.aria-label]="sideName() + (won() ? ', declared winner' : '')">
      <span class="pp-display name">{{ sideName() }}</span>
      @if (won()) { <mat-icon aria-hidden="true">emoji_events</mat-icon> }
    </article>
  `,
})
export class CompetitorPanelComponent {
  readonly side = input.required<'meron' | 'wala'>();
  readonly match = input<Match | null>(null);
  readonly sideName = computed(() => this.side() === 'meron' ? 'MERON' : 'WALA');
  readonly won = computed(() => this.match()?.result?.outcome === this.side());
  readonly lost = computed(() => {
    const outcome = this.match()?.result?.outcome;
    return (outcome === 'meron' || outcome === 'wala') && outcome !== this.side();
  });
}

import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import type { Match } from '@shared/models';
import { PICK_LABEL, PICK_SIDES, type PickSide } from '../core/player-picks';

/** Local side selection. It never transfers, deducts or awards points. */
@Component({
  selector: 'pp-side-picker',
  imports: [MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; }
    .sides { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.65rem; }
    label { position: relative; min-width: 0; cursor: pointer; }
    input { position: absolute; inset: 0; width: 100%; height: 100%; margin: 0; opacity: 0; cursor: inherit; z-index: 1; }
    .card { display: flex; align-items: center; justify-content: center; min-height: 108px; padding: 1rem 0.75rem;
      border: 1px solid color-mix(in srgb, var(--side) 65%, var(--pp-rope)); border-radius: 0.75rem;
      background: color-mix(in srgb, var(--side) 14%, var(--pp-pit)); color: var(--side-text);
      transition: border-color 180ms ease, background 180ms ease, box-shadow 180ms ease; }
    .name { font-size: clamp(1.75rem, 3vw, 2.6rem); text-transform: uppercase; }
    .meron { --side: var(--pp-meron); --side-text: var(--meron-text); }
    .wala { --side: var(--pp-wala); --side-text: var(--wala-text); }
    .draw { --side: var(--draw); --side-text: var(--success-text); grid-column: 1 / -1; }
    .draw .card { min-height: 64px; }
    .draw .name { font-size: clamp(1.35rem, 2.2vw, 1.8rem); }
    label:not(.locked):hover .card { background: color-mix(in srgb, var(--side) 25%, var(--pp-pit)); border-color: var(--side); }
    input:focus-visible + .card { outline: 2px solid var(--pp-chalk); outline-offset: 3px; }
    .selected .card { background: color-mix(in srgb, var(--side) 78%, var(--pp-pit)); color: var(--white);
      border-color: var(--side); box-shadow: 0 0 0 1px var(--side); }
    .draw.selected .card, .draw.selected .check { color: var(--on-accent); }
    .check { position: absolute; top: 0.6rem; right: 0.6rem; width: 18px; height: 18px; font-size: 18px; color: white; }
    .locked { cursor: default; }
    .locked:not(.selected) .card { opacity: 0.65; }
    @media (max-width: 479px) { .card { min-height: 88px; } .name { font-size: 2rem; } .draw .card { min-height: 56px; } .draw .name { font-size: 1.5rem; } }
  `,
  template: `
    <div class="sides" role="radiogroup" [attr.aria-label]="match() ? 'Side for fight ' + match()!.match_number : 'Match side'">
      @for (side of sides; track side) {
        <label [class]="side" [class.selected]="picked() === side" [class.locked]="!open()">
          <input type="radio" name="match-side" [value]="side" [checked]="picked() === side" [disabled]="!open()"
                 [attr.aria-label]="label[side]" (change)="choose.emit(side)" />
          <span class="card">
            <span class="pp-display name">{{ label[side] }}</span>
            @if (picked() === side) { <mat-icon class="check" aria-hidden="true">check_circle</mat-icon> }
          </span>
        </label>
      }
    </div>
  `,
})
export class SidePickerComponent {
  readonly match = input<Match | null>(null);
  readonly picked = input<PickSide | null>(null);
  readonly open = input(true);
  readonly choose = output<PickSide>();
  readonly sides = PICK_SIDES;
  readonly label = PICK_LABEL;
}

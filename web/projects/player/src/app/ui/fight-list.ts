import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { Match } from '@shared/models';
import { OUTCOME_TEXT, beadKind, describeMatch, matchPhase } from '../core/match-phase';
import { PhasePillComponent } from './phase-pill';

/** Fight card rows: number, Meron against Wala, then the result or the fight's state. */
@Component({
  selector: 'pp-fight-list',
  imports: [RouterLink, DatePipe, PhasePillComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    ul { list-style: none; margin: 0; padding: 0; }
    li + li { border-top: 1px solid var(--pp-rope); }
    a { display: grid; grid-template-columns: 3rem minmax(0, 1fr) auto; align-items: center; gap: 0.75rem; padding: 0.7rem 0.9rem;
      color: inherit; text-decoration: none; }
    a:hover, a:focus-visible { background: var(--pp-pit-raised); }
    .no { font-size: 1.5rem; color: var(--pp-ash); text-align: center; }
    .live .no { color: var(--pp-lamp); }
    .pair { display: flex; flex-direction: column; gap: 0.15rem; min-width: 0; }
    .c { display: flex; align-items: center; gap: 0.5rem; min-width: 0; }
    .c span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .c i { width: 0.5rem; height: 0.5rem; border-radius: 2px; flex: none; }
    .c.m i { background: var(--pp-meron); } .c.w i { background: var(--pp-wala); }
    .c.won span { color: var(--pp-chalk); font-weight: 600; }
    .c.lost span { color: var(--pp-ash); }
    .end { display: flex; flex-direction: column; align-items: flex-end; gap: 0.2rem; }
    .result { display: inline-flex; align-items: center; gap: 0.4rem; font-weight: 600; font-size: 0.85rem; white-space: nowrap; }
    .result i { width: 0.6rem; height: 0.6rem; border-radius: 50%; }
    .result.meron i { background: var(--pp-meron); } .result.wala i { background: var(--pp-wala); }
    .result.draw i { background: var(--pp-draw); } .result.void i { background: var(--pp-void); }
    .result.void { color: var(--pp-ash); }
    .meta { color: var(--pp-ash); font-size: 0.75rem; white-space: nowrap; }
    .empty { padding: 1.25rem 0.9rem; color: var(--pp-ash); font-size: 0.875rem; }
  `,
  template: `
    <ul>
      @for (m of matches(); track m.id) {
        @let kind = kindOf(m);
        <li [class.live]="kind === 'live'">
          <a [routerLink]="['/matches', m.id]" [attr.aria-label]="describe(m)">
            <span class="pp-num no">{{ m.match_number }}</span>
            <span class="pair">
              <span class="c m" [class.won]="kind === 'meron'" [class.lost]="kind === 'wala'"><i aria-hidden="true"></i><span>MERON</span></span>
              <span class="c w" [class.won]="kind === 'wala'" [class.lost]="kind === 'meron'"><i aria-hidden="true"></i><span>WALA</span></span>
            </span>
            <span class="end">
              @if (m.result; as r) {
                <span class="result" [class]="kind"><i aria-hidden="true"></i>{{ outcome(r.outcome) }}</span>
                @if (showEvent() && m.event_name) { <span class="meta">{{ m.event_name }}</span> }
                @else { <span class="meta">{{ r.declared_at | date: 'MMM d, h:mm a' }}@if (r.revision > 1) {, corrected}</span> }
              } @else if (m.status === 'cancelled') {
                <span class="result void"><i aria-hidden="true"></i>Cancelled</span>
              } @else {
                <pp-phase-pill [phase]="phase(m)" />
                @if (m.scheduled_at) { <span class="meta">{{ m.scheduled_at | date: 'h:mm a' }}</span> }
              }
            </span>
          </a>
        </li>
      } @empty {
        <li class="empty">{{ emptyText() }}</li>
      }
    </ul>
  `,
})
export class FightListComponent {
  readonly matches = input.required<Match[]>();
  readonly showEvent = input(false);
  readonly emptyText = input('No fights on the card yet.');

  readonly kindOf = beadKind;
  readonly phase = matchPhase;
  readonly describe = describeMatch;
  readonly outcome = (o: keyof typeof OUTCOME_TEXT) => OUTCOME_TEXT[o];
}

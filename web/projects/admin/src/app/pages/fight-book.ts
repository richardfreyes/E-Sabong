import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';

import type { AdminFightBook, EventItem, Match } from '@shared/models';
import { ArenaPointsPipe } from '@shared/pipes';

/** One fight's betting state, all-player pools and live multipliers, with the open/close controls. */
@Component({
  selector: 'app-fight-book',
  imports: [MatButtonModule, MatIconModule, ArenaPointsPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; width: 100%; }
    .book { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.6rem 0.7rem; border: 1px solid var(--mat-sys-outline-variant); border-radius: 0.6rem; }
    .head { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
    .state { font-size: 0.7rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--mat-sys-on-surface-variant); }
    .state.open { color: var(--success-text, #6ee7b7); }
    .state.closed { color: var(--warning-text, #fcd34d); }
    // Columns reflow on narrow cards; amounts stay whole instead of breaking mid-number.
    dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(6.25rem, 1fr)); gap: 0.4rem; margin: 0; }
    dl div { min-width: 0; padding-left: 0.45rem; border-left: 3px solid var(--side); }
    .meron { --side: var(--meron); } .wala { --side: var(--wala); } .draw { --side: var(--draw); }
    dt { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--mat-sys-on-surface-variant); }
    dd { margin: 0; font-size: 0.8rem; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
    dd small { display: block; font-weight: 500; color: var(--mat-sys-on-surface-variant); }
    .meta { margin: 0; font-size: 0.7rem; color: var(--mat-sys-on-surface-variant); }
  `,
  template: `
    <div class="book" role="group" [attr.aria-label]="'Betting for match ' + match().match_number">
      <div class="head">
        <span class="state" [class]="'state ' + (book()?.state ?? 'none')">{{ stateLabel() }}</span>
        @if (canManage()) {
          @if (book()?.state === 'open') {
            <button mat-stroked-button type="button" (click)="close.emit()"><mat-icon>lock</mat-icon>Close betting</button>
          } @else if (canOpen()) {
            <button mat-stroked-button type="button" (click)="open.emit()"><mat-icon>lock_open</mat-icon>{{ book() ? 'Reopen betting' : 'Open betting' }}</button>
          }
        }
      </div>
      @if (book(); as b) {
        <dl>
          <div class="meron"><dt>Meron</dt><dd>{{ b.pools.meron | arenaPoints }}<small>Odds {{ b.odds.meron_display ?? '—' }}</small></dd></div>
          <div class="wala"><dt>Wala</dt><dd>{{ b.pools.wala | arenaPoints }}<small>Odds {{ b.odds.wala_display ?? '—' }}</small></dd></div>
          <div class="draw"><dt>Draw</dt><dd>{{ b.pools.draw | arenaPoints }}<small>× {{ drawMultiplier() }}</small></dd></div>
        </dl>
        <p class="meta">
          {{ b.bet_count }} {{ b.bet_count === 1 ? 'bet' : 'bets' }} · {{ b.final ? 'final multipliers' : 'provisional multipliers' }}
          @if (b.commission_amount !== null) { · deduction {{ b.commission_amount | arenaPoints }} }
        </p>
      }
    </div>
  `,
})
export class FightBookComponent {
  readonly match = input.required<Match>();
  readonly event = input.required<EventItem>();
  readonly book = input<AdminFightBook | null>(null);
  readonly canManage = input(false);
  readonly open = output<void>();
  readonly close = output<void>();

  readonly canOpen = computed(() => {
    const m = this.match();
    const state = this.book()?.state;
    return this.event().status === 'live' && (m.status === 'scheduled' || m.status === 'in_progress') && !m.result && state !== 'settled';
  });
  readonly stateLabel = computed(() => {
    const b = this.book();
    if (!b?.state) return 'Betting not opened';
    if (b.state === 'settled') return `Settled · ${(b.outcome ?? '').replace('_', ' ')}`;
    return b.state === 'open' ? (b.accepting ? 'Betting open' : 'Open · not accepting') : 'Betting closed';
  });
  readonly drawMultiplier = computed(() => String(Number(this.book()?.draw_multiplier ?? 0)));
}

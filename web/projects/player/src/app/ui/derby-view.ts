import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';
import { Router } from '@angular/router';

import type { Match } from '@shared/models';
import { ArenaStore } from '../core/arena.store';
import { PlayerPrefs } from '../core/player-prefs';
import { DerbyRecordComponent } from './derby-record';
import { FightListComponent } from './fight-list';
import { WinningHistoryBoardComponent } from './winning-history-board';

/** Record, result board and fight card of the event held by the surrounding ArenaStore. */
@Component({
  selector: 'pp-derby-view',
  imports: [DerbyRecordComponent, WinningHistoryBoardComponent, FightListComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: flex; flex-direction: column; gap: 1rem; }
    .record { padding: 0 1.1rem 1.1rem; }
    .skeleton { height: 9rem; margin: 0 1.1rem 1.1rem; border-radius: 0.75rem; background: var(--pp-pit-raised); animation: pp-pulse 1.6s ease-in-out infinite; }
  `,
  template: `
    <section class="pp-panel" aria-labelledby="record-title">
      <div class="pp-panel-head"><h2 id="record-title" class="pp-h2">Derby record</h2>
        <span class="pp-muted text-sm">{{ store.matches().length }} {{ store.matches().length === 1 ? 'fight' : 'fights' }} on the card</span></div>
      @if (store.loading() && !store.matches().length) {
        <div class="skeleton" aria-label="Loading results"></div>
      } @else {
        <div class="record"><pp-derby-record [stats]="store.stats()" /></div>
        <pp-winning-history-board [matches]="store.matches()" [eventId]="store.eventId()" [reduceMotion]="prefs.reduceMotion()" (selected)="open($event)" />
      }
    </section>
    @if (showCard()) {
      <section class="pp-panel" aria-labelledby="card-title">
        <div class="pp-panel-head"><h2 id="card-title" class="pp-h2">Fight card</h2></div>
        <pp-fight-list [matches]="store.matches()" />
      </section>
    }
  `,
})
export class DerbyViewComponent {
  readonly showCard = input(true);
  readonly store = inject(ArenaStore);
  readonly prefs = inject(PlayerPrefs);
  private readonly router = inject(Router);

  open(m: Match): void {
    void this.router.navigate(['/matches', m.id]);
  }
}

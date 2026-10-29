import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { Router, RouterLink } from '@angular/router';

import type { Match } from '@shared/models';
import { ArenaStore } from '../core/arena.store';
import { OUTCOME_TEXT, beadKind, eventPhase, matchPhase } from '../core/match-phase';
import { PlayerPicks, type PickSide } from '../core/player-picks';
import { PlayerPrefs } from '../core/player-prefs';
import { PlayerSession } from '../core/player-session';
import { AmountPickerComponent } from '../ui/amount-picker';
import { DerbyRecordComponent } from '../ui/derby-record';
import { PhasePillComponent } from '../ui/phase-pill';
import { ConnectionStatusComponent } from '../ui/connection-status';
import { WinningHistoryBoardComponent } from '../ui/winning-history-board';
import { SidePickerComponent } from '../ui/side-picker';
import { VideoStageComponent } from '../ui/video-stage';

/**
 * The arena, read top to bottom: the video display, the fight number, both
 * corners to pick between, the demonstration amount, then the derby record.
 */
@Component({
  selector: 'app-player-arena',
  imports: [
    DatePipe, RouterLink, MatButtonModule, MatIconModule, MatMenuModule, VideoStageComponent, SidePickerComponent, AmountPickerComponent,
    ConnectionStatusComponent, PhasePillComponent, DerbyRecordComponent, WinningHistoryBoardComponent,
  ],
  providers: [ArenaStore],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './dashboard.scss',
  template: `
    <div class="arena">
      <div class="viewing">
        <pp-video-stage class="video" [event]="store.event()" [match]="store.current()" [announcement]="store.announcement()"
                        [loading]="store.loading()" [storeError]="store.error()" (refresh)="store.refresh()"
                        (dismiss)="store.dismissAnnouncement()" />
        <section class="pp-panel context" aria-label="Match information" aria-live="polite">
          <div class="context-item">
            <h2 class="context-label">Official result</h2>
            @if (store.current()?.result; as r) {
              <span class="context-value" [class]="kindOf(store.current()!)"><i class="result-dot" aria-hidden="true"></i>{{ outcome(r.outcome) }}</span>
              <span class="context-meta">{{ r.declared_at | date: 'h:mm a' }}@if (r.revision > 1) { · Corrected}</span>
            } @else {
              <span class="context-value">{{ store.current()?.status === 'cancelled' ? 'Cancelled' : 'Awaiting result' }}</span>
            }
          </div>
          <div class="context-item">
            <h2 class="context-label">Previous fight</h2>
            @if (store.previous(); as p) {
              <a class="context-value" [routerLink]="['/matches', p.id]">Fight #{{ p.match_number }}</a>
              <span class="context-meta">{{ outcome(p.result!.outcome) }}</span>
            } @else { <span class="context-value">No results yet</span> }
          </div>
          <div class="context-item">
            <h2 class="context-label">Up next</h2>
            @if (store.next(); as n) {
              <a class="context-value" [routerLink]="['/matches', n.id]">Fight #{{ n.match_number }}</a>
              <span class="context-meta">{{ n.scheduled_at ? (n.scheduled_at | date: 'h:mm a') : 'Scheduled' }}</span>
            } @else { <span class="context-value">{{ store.event() ? 'End of card' : 'No scheduled fights' }}</span> }
          </div>
        </section>
      </div>
      <div class="controls">
        <header class="head">
          <div class="min-w-0">
            <h1 class="fight-no pp-display" aria-live="polite">
              <span class="word">FIGHT</span><span class="no pp-num">{{ store.current() ? '#' + store.current()!.match_number : '—' }}</span>
            </h1>
            <p class="where">
              <span class="name">{{ store.event()?.name ?? (store.loading() ? 'Loading event' : 'No scheduled event') }}</span>
              @if (store.event(); as e) { <span>{{ e.starts_at | date: 'd MMM y, h:mm a' }}</span> }
            </p>
          </div>
          <div class="head-side">
            @if (store.current(); as m) { <pp-phase-pill [phase]="matchPhaseOf(m)" /> }
            @else if (store.event(); as e) { <pp-phase-pill [phase]="eventPhaseOf(e)" /> }
            @if (store.events().length > 1) {
              <button mat-stroked-button type="button" [matMenuTriggerFor]="events" class="switch"><mat-icon>swap_horiz</mat-icon>Switch event</button>
              <mat-menu #events="matMenu" xPosition="before">
                @for (e of store.events(); track e.id) {
                  <button mat-menu-item type="button" (click)="store.select(e.id)" [class.picked]="e.id === store.eventId()">
                    <span class="menu-event">{{ e.name }}</span><span class="menu-meta">{{ eventPhaseOf(e).label }} · {{ e.starts_at | date: 'MMM d' }}</span>
                  </button>
                }
              </mat-menu>
            }
          </div>
        </header>
        <section class="sides" aria-label="Match sides">
          <pp-side-picker [match]="store.current()" [picked]="pickedSide()" [open]="picksOpen()" (choose)="choose($event)" />
        </section>
        <section class="amount" aria-labelledby="amount-title">
          <div class="amount-head"><h2 id="amount-title" class="pp-h2">Amount</h2><span class="amount-unit">Demo points</span></div>
          <pp-amount-picker [amount]="picks.amount()" [available]="session.balance()" (amountChange)="setAmount($event)" />
        </section>
      </div>
      <section class="pp-panel records" aria-labelledby="record-title">
        <div class="pp-panel-head">
          <div class="record-heading"><h2 id="record-title" class="pp-h2">Match records</h2><span class="record-count">{{ store.stats().total }} decided</span></div>
          @if (store.eventId()) {
            <a routerLink="/history" [queryParams]="{ event: store.eventId() }" class="history-link">View history<mat-icon aria-hidden="true">arrow_forward</mat-icon></a>
          }
        </div>
        @if (store.loading() && !store.matches().length) {
          <div class="record-skeleton" aria-label="Loading results"></div>
        } @else {
          <pp-winning-history-board [matches]="store.matches()" [eventId]="store.eventId()" [reduceMotion]="prefs.reduceMotion()" (selected)="open($event)" />
          <div class="record-stats"><pp-derby-record [stats]="store.stats()" /></div>
        }
      </section>
      <footer class="statusbar">
        <pp-connection-status />
        <span>Demo points · No cash value · Non-redeemable</span>
        <a routerLink="/notifications" aria-label="View notifications"><mat-icon aria-hidden="true">notifications_none</mat-icon></a>
      </footer>
    </div>
  `,
})
export class DashboardPage {
  readonly store = inject(ArenaStore);
  readonly prefs = inject(PlayerPrefs);
  readonly picks = inject(PlayerPicks);
  readonly session = inject(PlayerSession);
  private readonly router = inject(Router);

  readonly eventPhaseOf = eventPhase;
  readonly matchPhaseOf = matchPhase;
  readonly kindOf = beadKind;
  readonly outcome = (o: keyof typeof OUTCOME_TEXT) => OUTCOME_TEXT[o];
  readonly currentId = computed(() => this.store.current()?.id ?? null);

  readonly currentPick = computed(() => this.picks.pickFor(this.currentId()));
  readonly pickedSide = computed(() => this.currentPick()?.side ?? null);
  /** A side can only be marked while the fight is still to be decided. */
  readonly picksOpen = computed(() => {
    const m = this.store.current();
    return !!m && (m.status === 'scheduled' || m.status === 'in_progress');
  });

  constructor() {
    this.store.init();
  }

  choose(side: PickSide): void {
    const m = this.store.current();
    if (m && this.picksOpen()) this.picks.choose(m.id, side, this.picks.amount());
  }

  setAmount(amount: number): void {
    this.picks.amount.set(amount);
    const m = this.store.current();
    const pick = this.picks.pickFor(m?.id);
    if (m && pick && this.picksOpen()) this.picks.choose(m.id, pick.side, amount);
  }

  open(m: Match): void {
    void this.router.navigate(['/matches', m.id]);
  }
}

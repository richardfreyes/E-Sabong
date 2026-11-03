import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { Router, RouterLink } from '@angular/router';

import type { Match } from '@shared/models';
import { ArenaStore } from '../core/arena.store';
import { OUTCOME_TEXT, beadKind, eventPhase, matchPhase } from '../core/match-phase';
import { PICK_LABEL, PICK_OUTCOME_LABEL, PlayerPicks, pickOutcome, type PickSide } from '../core/player-picks';
import { PlayerPrefs } from '../core/player-prefs';
import { PlayerSession } from '../core/player-session';
import { FightBookStore, PlayWalletStore } from '../core/play-betting';
import { FightBettingPanelComponent, type BetRequest } from '../ui/fight-betting-panel';
import { FightHeaderComponent } from '../ui/fight-header';
import { DerbyRecordComponent } from '../ui/derby-record';
import { PhasePillComponent } from '../ui/phase-pill';
import { ConnectionStatusComponent } from '../ui/connection-status';
import { WinningHistoryBoardComponent } from '../ui/winning-history-board';
import { SidePickerComponent } from '../ui/side-picker';
import { VideoStageComponent } from '../ui/video-stage';

/**
 * The arena, read top to bottom: the video display, the fight number, both
 * corners to pick between (or, with the play balance on, the betting panel), then the derby record.
 */
@Component({
  selector: 'app-player-arena',
  imports: [
    DatePipe, RouterLink, MatButtonModule, MatIconModule, MatMenuModule, VideoStageComponent, SidePickerComponent,
    ConnectionStatusComponent, PhasePillComponent, DerbyRecordComponent, WinningHistoryBoardComponent, FightBettingPanelComponent, FightHeaderComponent,
  ],
  providers: [ArenaStore, FightBookStore],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './dashboard.scss',
  template: `
    <div class="arena" [class.betting-on]="wallet.enabled()">
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
              <span class="context-meta">{{ outcome(p.result!.outcome) }}@if (previousOutcome(); as o) { · Your pick {{ outcomeLabel[o] }}}</span>
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
      <div class="controls" [class.betting-on]="wallet.enabled()">
        <!-- The one event picker, opened from either header. -->
        <mat-menu #events="matMenu" xPosition="before">
          @for (e of store.events(); track e.id) {
            <button mat-menu-item type="button" (click)="store.select(e.id)" [class.picked]="e.id === store.eventId()">
              <span class="menu-event">{{ e.name }}</span><span class="menu-meta">{{ eventPhaseOf(e).label }} · {{ e.starts_at | date: 'MMM d' }}</span>
            </button>
          }
        </mat-menu>
        @if (wallet.enabled()) {
          <header class="bet-head">
            <pp-fight-header [match]="store.current()" [event]="store.event()" [loading]="store.loading()"
                             [menu]="store.events().length > 1 ? events : null" />
          </header>
        } @else {
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
            }
          </div>
        </header>
        }
        @if (wallet.enabled()) {
          <section class="betting" aria-label="Betting">
            <pp-fight-betting-panel [book]="fight.book()" [mine]="fight.mine()" [limits]="fight.limits()" [balance]="wallet.balance()"
                                    [placing]="fight.placing()" [loading]="fight.loading()" [notice]="fight.notice()" [error]="fight.error()"
                                    (bet)="placeBet($event)" (retry)="fight.reload()" />
          </section>
        } @else {
        <section class="sides" aria-label="Match sides">
          <pp-side-picker [match]="store.current()" [picked]="pickedSide()" [open]="picksOpen()" (choose)="choose($event)" />
          @if (currentPick(); as pick) {
            <p class="pick-status" [class]="'pick-status ' + (currentOutcome() ?? 'open')" role="status" aria-live="polite">
              <span>Your pick <strong>{{ pickLabel[pick.side] }}</strong></span>
              @if (currentOutcome(); as o) { <span class="pick-outcome">{{ outcomeLabel[o] }}</span> }
              @else { <span class="pick-wait">{{ picks.saving() === pick.match_id ? 'Saving…' : pick.open ? 'Awaiting official result' : 'Closed · awaiting official result' }}</span> }
            </p>
          }
          @if (picks.error(); as error) { <p class="pick-error" role="alert">{{ error }}</p> }
          @if (wallet.config() && !wallet.enabled()) {
            <p class="betting-off" role="note"><mat-icon aria-hidden="true">info</mat-icon>Simulated betting is turned off for this workspace. Predictions work as usual.</p>
          }
        </section>
        }
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
        <span>{{ wallet.enabled() ? 'Simulated play · No cash value · Not redeemable' : 'Predictions are free · No cash value' }}</span>
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
  readonly wallet = inject(PlayWalletStore);
  readonly fight = inject(FightBookStore);
  private readonly router = inject(Router);

  readonly eventPhaseOf = eventPhase;
  readonly matchPhaseOf = matchPhase;
  readonly kindOf = beadKind;
  readonly outcome = (o: keyof typeof OUTCOME_TEXT) => OUTCOME_TEXT[o];
  readonly currentId = computed(() => this.store.current()?.id ?? null);

  readonly currentPick = computed(() => this.picks.pickFor(this.currentId()));
  readonly pickedSide = computed(() => this.currentPick()?.side ?? null);
  readonly pickLabel = PICK_LABEL;
  readonly outcomeLabel = PICK_OUTCOME_LABEL;
  readonly currentOutcome = computed(() => pickOutcome(this.currentPick()));
  readonly previousOutcome = computed(() => pickOutcome(this.picks.pickFor(this.store.previous()?.id)));
  /** A side can only be marked while the fight is still to be decided; the server has the final say. */
  readonly picksOpen = computed(() => {
    const m = this.store.current();
    const pick = this.currentPick();
    return !!m && (m.status === 'scheduled' || m.status === 'in_progress') && (!pick || pick.open) && this.picks.saving() !== m.id;
  });

  constructor() {
    this.store.init();
    effect(() => {
      const eventId = this.store.eventId();
      if (eventId) this.picks.loadEvent(eventId);
    });
    // The betting board follows the fight on stage.
    effect(() => {
      if (this.wallet.enabled()) this.fight.watch(this.currentId());
    });
  }

  placeBet(request: BetRequest): void {
    void this.fight.place(request.side, request.amount);
  }

  choose(side: PickSide): void {
    const m = this.store.current();
    if (m && this.picksOpen()) void this.picks.choose(m.id, side);
  }

  open(m: Match): void {
    void this.router.navigate(['/matches', m.id]);
  }
}

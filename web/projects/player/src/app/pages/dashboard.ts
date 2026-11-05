import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { Router, RouterLink } from '@angular/router';

import type { Match } from '@shared/models';
import { PortalApi } from '@shared/services';
import { ArenaStore } from '../core/arena.store';
import { OUTCOME_TEXT, beadKind, eventPhase, matchPhase } from '../core/match-phase';
import { PICK_LABEL, PICK_OUTCOME_LABEL, PlayerPicks, pickOutcome, type PickSide } from '../core/player-picks';
import { PlayerPrefs } from '../core/player-prefs';
import { PlayerSession } from '../core/player-session';
import { FightBookStore, PlayWalletStore } from '../core/play-betting';
import { FightBettingPanelComponent, type BetRequest } from '../ui/fight-betting-panel';
import { FightHeaderComponent } from '../ui/fight-header';
import { BeadPlateComponent, type SidePools } from '../ui/bead-plate';
import { PhasePillComponent } from '../ui/phase-pill';
import { ConnectionStatusComponent } from '../ui/connection-status';
import { WinningHistoryBoardComponent } from '../ui/winning-history-board';
import { SidePickerComponent } from '../ui/side-picker';
import { VideoStageComponent } from '../ui/video-stage';

/**
 * The arena board. Desktop: the event title and video on the left; the fight strip, both corners
 * (or, with the play balance on, the betting panel), the streak road, the result tally and the
 * bead plate on the right. Tablet and phone: one column in that reading order.
 */
@Component({
  selector: 'app-player-arena',
  imports: [
    DatePipe, RouterLink, MatIconModule, MatMenuModule, VideoStageComponent, SidePickerComponent,
    ConnectionStatusComponent, PhasePillComponent, BeadPlateComponent, WinningHistoryBoardComponent, FightBettingPanelComponent, FightHeaderComponent,
  ],
  providers: [ArenaStore, FightBookStore],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './dashboard.scss',
  template: `
    <div class="arena" [class.betting-on]="wallet.enabled()">
      <section class="stage" aria-label="Live video">
        <header class="title-bar">
          <h2 class="event-title">{{ store.event()?.name ?? (store.loading() ? 'Loading event' : 'No scheduled event') }}</h2>
          @if (store.event(); as e) { <span class="event-date">{{ e.starts_at | date: 'MMMM d, y' }}</span> }
        </header>
        <pp-video-stage class="video" [event]="store.event()" [match]="store.current()" [announcement]="store.announcement()"
                        [loading]="store.loading()" [storeError]="store.error()" (refresh)="store.refresh()"
                        (dismiss)="store.dismissAnnouncement()" />
        <section class="context" aria-label="Match information" aria-live="polite">
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
      </section>

      <section class="board" aria-label="Fight board">
        <!-- The one event picker, opened from either header. -->
        <mat-menu #events="matMenu" xPosition="before">
          @for (e of store.events(); track e.id) {
            <button mat-menu-item type="button" (click)="store.select(e.id)" [class.picked]="e.id === store.eventId()">
              <span class="menu-event">{{ e.name }}</span><span class="menu-meta">{{ eventPhaseOf(e).label }} · {{ e.starts_at | date: 'MMM d' }}</span>
            </button>
          }
        </mat-menu>
        @if (wallet.enabled()) {
          <pp-fight-header class="bet-head" [match]="store.current()" [event]="store.event()" [loading]="store.loading()" [book]="fight.book()"
                           [menu]="store.events().length > 1 ? events : null" />
          <pp-fight-betting-panel class="betting" aria-label="Betting" role="region"
                                  [book]="fight.book()" [mine]="fight.mine()" [limits]="fight.limits()" [balance]="wallet.balance()"
                                  [placing]="fight.placing()" [loading]="fight.loading()" [notice]="fight.notice()" [error]="fight.error()"
                                  (bet)="placeBet($event)" (retry)="fight.reload()" />
        } @else {
          <header class="head">
            <h1 class="fight-no" aria-live="polite">Fight {{ store.current() ? '#' + store.current()!.match_number : '—' }}</h1>
            <span class="grow"></span>
            @if (store.events().length > 1) {
              <button type="button" [matMenuTriggerFor]="events" class="switch" aria-label="Switch event"><mat-icon aria-hidden="true">swap_horiz</mat-icon><span>Switch event</span></button>
            }
            @if (store.current(); as m) { <pp-phase-pill [phase]="matchPhaseOf(m)" /> }
            @else if (store.event(); as e) { <pp-phase-pill [phase]="eventPhaseOf(e)" /> }
          </header>
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

        <section class="records" aria-labelledby="record-title">
          <div class="records-head">
            <h2 id="record-title" class="records-title">Match records</h2>
            <span class="record-count">{{ store.stats().total }} decided</span>
            <span class="grow"></span>
            @if (store.eventId()) {
              <a routerLink="/history" [queryParams]="{ event: store.eventId() }" class="history-link">View history<mat-icon aria-hidden="true">arrow_forward</mat-icon></a>
            }
          </div>
          @if (store.loading() && !store.matches().length) {
            <div class="record-skeleton" aria-label="Loading results"></div>
          } @else {
            <pp-winning-history-board class="trend" [matches]="store.matches()" [eventId]="store.eventId()" [reduceMotion]="prefs.reduceMotion()" (selected)="open($event)" />
            <dl class="tally" role="img" [attr.aria-label]="tallySummary()">
              @for (t of tally(); track t.key) {
                <div class="tally-card" [class]="'tally-card ' + t.key">
                  <dt>{{ t.label }}</dt><dd class="tally-n">{{ t.count }}</dd>
                  <dd class="tally-bar"><span class="fill" [style.width.%]="t.pct"></span><span class="pct">{{ t.pct.toFixed(2) }}%</span></dd>
                </div>
              }
            </dl>
            <pp-bead-plate class="plate" [matches]="store.matches()" [pools]="pools()" (selected)="open($event)" />
          }
        </section>
      </section>

      <footer class="statusbar">
        <pp-connection-status />
        <span class="status-chip note">{{ wallet.enabled() ? 'Simulated play · No cash value · Not redeemable' : 'Predictions are free · No cash value' }}</span>
        <a routerLink="/notifications" class="status-chip link" aria-label="View notifications"><mat-icon aria-hidden="true">notifications_none</mat-icon><span>Notifications</span></a>
      </footer>
    </div>
  `,
})
export class DashboardPage {
  readonly store = inject(ArenaStore);
  private readonly api = inject(PortalApi);
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
  /** All-player MERON/WALA totals by match, for the L/D marks on the history plate. */
  readonly pools = signal<Record<string, SidePools>>({});
  private readonly cardStamp = computed(() => this.store.matches().map((m) => `${m.id}:${m.status}:${m.result?.outcome ?? ''}:${m.result?.revision ?? 0}`).join('|'));

  /** Result tally cards in board order: MERON, CANCELLED, DRAW, WALA. */
  readonly tally = computed(() => {
    const s = this.store.stats();
    return [
      { key: 'meron', label: 'Meron', count: s.meron, pct: s.pct(s.meron) },
      { key: 'void', label: 'Cancelled', count: s.voided, pct: s.pct(s.voided) },
      { key: 'draw', label: 'Draw', count: s.draw, pct: s.pct(s.draw) },
      { key: 'wala', label: 'Wala', count: s.wala, pct: s.pct(s.wala) },
    ];
  });
  readonly tallySummary = computed(() => {
    const s = this.store.stats();
    if (!s.total) return 'No fights decided yet';
    return `${s.total} fights decided: Meron ${s.meron}, Wala ${s.wala}, draws ${s.draw}, cancelled ${s.voided}`;
  });

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
    effect((onCleanup) => {
      const eventId = this.store.eventId();
      this.cardStamp();
      if (!eventId) {
        this.pools.set({});
        return;
      }
      const sub = this.api.eventPools(eventId).subscribe({
        next: (rows) => {
          const next: Record<string, SidePools> = {};
          for (const row of rows) next[row.match_id] = { meron: String(row.meron), wala: String(row.wala) };
          const live = this.fight.book();
          if (live) next[live.match_id] = { meron: live.pools.meron, wala: live.pools.wala };
          this.pools.set(next);
        },
        error: () => undefined,
      });
      onCleanup(() => sub.unsubscribe());
    });
    effect(() => {
      const live = this.fight.book();
      if (!live) return;
      this.pools.update((current) => ({ ...current, [live.match_id]: { meron: live.pools.meron, wala: live.pools.wala } }));
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

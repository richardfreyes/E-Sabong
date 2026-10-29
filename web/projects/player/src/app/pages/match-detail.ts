import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, effect, inject, input, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';
import { RouterLink } from '@angular/router';

import type { Match } from '@shared/models';
import { PortalApi, RealtimeService, toApiError } from '@shared/services';
import { OUTCOME_TEXT, beadKind, matchPhase } from '../core/match-phase';
import { PICK_LABEL, PICK_OUTCOME_LABEL, PlayerPicks, pickOutcome } from '../core/player-picks';
import { CompetitorPanelComponent } from '../ui/competitor-panel';
import { MatchTimelineComponent } from '../ui/match-timeline';
import { PageHeadComponent } from '../ui/page-head';
import { PhasePillComponent } from '../ui/phase-pill';

/** One fight in full, updating live when its status or result changes. */
@Component({
  selector: 'app-player-match',
  imports: [DatePipe, RouterLink, MatIconModule, PageHeadComponent, PhasePillComponent, CompetitorPanelComponent, MatchTimelineComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .back { display: inline-flex; align-items: center; gap: 0.25rem; margin-bottom: 0.4rem; color: var(--pp-ash); text-decoration: none; font-size: 0.85rem; }
    .back:hover { color: var(--pp-chalk); }
    .back mat-icon { width: 18px; height: 18px; font-size: 18px; }
    .layout { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 1rem; align-items: start; }
    .corners { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
    .result { display: flex; align-items: center; gap: 0.75rem; padding: 1rem 1.1rem; }
    .result i { width: 1rem; height: 1rem; border-radius: 50%; background: var(--pp-void); flex: none; }
    .result.meron i { background: var(--pp-meron); } .result.wala i { background: var(--pp-wala); } .result.draw i { background: var(--pp-draw); }
    .result-text { font-size: 2.4rem; color: var(--pp-chalk); }
    .result-time { margin-left: auto; color: var(--pp-ash); font-size: 0.85rem; text-align: right; }
    .stack { display: flex; flex-direction: column; gap: 1rem; }
    .body { padding: 0 1.1rem 1.1rem; }
    .pick { color: var(--text-secondary); } .pick strong { color: var(--text-primary); }
    .pick.win { color: var(--success-text); } .pick.lose { color: var(--error-text); } .pick.void { color: var(--cancelled-text); }
    .skeleton { height: 16rem; border-radius: 1.25rem; background: var(--pp-pit); animation: pp-pulse 1.6s ease-in-out infinite; }
    @media (max-width: 1023px) { .layout { grid-template-columns: minmax(0, 1fr); } }
    @media (max-width: 479px) { .corners { gap: 0.5rem; } .result-text { font-size: 1.9rem; } }
  `,
  template: `
    @if (error()) {
      <p class="pp-panel p-6 m-0" role="alert">This fight isn't available: {{ error() }} <a routerLink="/events" style="color: var(--mat-sys-primary)">See events</a></p>
    } @else if (!match()) {
      <div class="skeleton" aria-label="Loading fight"></div>
    } @else {
      @let m = match()!;
      <pp-page-head [title]="'Fight ' + m.match_number" [subtitle]="m.event_name">
        <a back [routerLink]="['/events', m.event_id]" class="back"><mat-icon aria-hidden="true">arrow_back</mat-icon>Fight card</a>
        <pp-phase-pill [phase]="phase(m)" />
      </pp-page-head>
      <div class="layout" aria-live="polite">
        <div class="stack">
          <div class="corners">
            <pp-competitor-panel side="meron" [match]="m" />
            <pp-competitor-panel side="wala" [match]="m" />
          </div>
          <section class="pp-panel" aria-labelledby="official">
            <div class="pp-panel-head"><h2 id="official" class="pp-h2">Official result</h2></div>
            @if (m.result; as r) {
              <p class="result m-0" [class]="kind(m)">
                <i aria-hidden="true"></i><span class="pp-display result-text">{{ outcome(r.outcome) }}</span>
                <span class="result-time">Declared {{ r.declared_at | date: 'MMM d, h:mm a' }}@if (r.revision > 1) {<br />Corrected after review}</span>
              </p>
              @if (r.notes) { <p class="body pp-muted m-0 text-sm">{{ r.notes }}</p> }
            } @else if (m.status === 'cancelled') {
              <p class="body pp-muted m-0">Cancelled{{ m.cancel_reason ? ': ' + m.cancel_reason : '' }}.</p>
            } @else {
              <p class="body pp-muted m-0">Not declared yet. This page updates the moment the officials announce it.</p>
            }
            @if (picks.pickFor(m.id); as pick) {
              @let o = pickOutcome(pick);
              <p class="body m-0 text-sm" [class]="'pick ' + (o ?? 'open')">Your pick <strong>{{ pickLabel[pick.side] }}</strong>{{ o ? ' · ' + outcomeLabel[o] : ' · Awaiting official result' }}</p>
            }
          </section>
        </div>
        <section class="pp-panel" aria-labelledby="timeline">
          <div class="pp-panel-head"><h2 id="timeline" class="pp-h2">Timeline</h2></div>
          <div class="body"><pp-match-timeline [match]="m" /></div>
        </section>
      </div>
    }
  `,
})
export class MatchDetailPage {
  readonly id = input.required<string>();
  private readonly api = inject(PortalApi);
  readonly match = signal<Match | null>(null);
  readonly error = signal<string | null>(null);
  readonly phase = matchPhase;
  readonly kind = beadKind;
  readonly outcome = (o: keyof typeof OUTCOME_TEXT) => OUTCOME_TEXT[o];
  readonly picks = inject(PlayerPicks);
  readonly pickOutcome = pickOutcome;
  readonly pickLabel = PICK_LABEL;
  readonly outcomeLabel = PICK_OUTCOME_LABEL;

  constructor() {
    effect(() => {
      this.id();
      untracked(() => this.load());
    });
    inject(RealtimeService)
      .on<{ id: string }>('match.updated', 'match.result_declared', 'match.result_corrected')
      .pipe(takeUntilDestroyed(inject(DestroyRef)))
      .subscribe((d) => d.id === this.id() && this.load());
  }

  load(): void {
    this.picks.loadMatch(this.id());
    this.api.match(this.id()).subscribe({ next: (m) => this.match.set(m), error: (e) => this.error.set(toApiError(e).message) });
  }
}

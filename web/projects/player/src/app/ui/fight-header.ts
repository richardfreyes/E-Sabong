import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { MatMenuModule, type MatMenuPanel } from '@angular/material/menu';

import type { EventItem, FightBook, Match } from '@shared/models';
import { eventPhase, matchPhase } from '../core/match-phase';

/**
 * Fight number, event line, phase pill and the event switcher, in the betting panel's design.
 * Rendered by the arena in its header slot, so it sits above the video on mobile and heads the
 * side panel on desktop. `menu` is the arena's existing event picker.
 */
@Component({
  selector: 'pp-fight-header',
  imports: [DatePipe, MatMenuModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: flex; align-items: center; gap: 10px; min-height: 48px; padding: 4px 12px; font-family: 'Noto Sans', var(--bet-font), sans-serif; color: var(--bet-text);
      font-variant-numeric: tabular-nums; background: #121212; }
    h1 { margin: 0; display: flex; align-items: baseline; gap: 6px; line-height: 1; font: 400 22px/1.27 'Bebas Neue', var(--font-display), sans-serif; text-transform: uppercase;
      letter-spacing: normal; white-space: nowrap; }
    .lbl, .num { font: inherit; }
    .grow { flex: 1; }
    .pill { display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 12px; border-radius: 4px; background: #3B877B;
      color: #FFF; font-size: 13px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; white-space: nowrap; }
    .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #C8E6C9; opacity: 1; }
    .pill.live { background: #FDD835; color: #000; }
    .pill.live .dot { background: #22C55E; }
    .pill.cancelled, .pill.closed, .pill.finished { background: rgb(255 255 255 / 0.12); }
    /* Betting state, as on the reference board: green OPEN, yellow CLOSED. */
    .pill.bet-open { background: #4CAF50; color: #FFF; }
    .pill.bet-open .dot { background: #E8F5E9; animation: pill-live 1.8s ease-in-out infinite; }
    .pill.bet-closed { background: #FDD835; color: #000; }
    .pill.bet-closed .dot { background: #76FF03; }
    @keyframes pill-live { 50% { opacity: 0.35; } }
    @media (prefers-reduced-motion: reduce) { .pill .dot { animation: none !important; } }
    .pill.cancelled .dot, .pill.closed .dot, .pill.finished .dot { background: currentColor; opacity: 0.7; }
    .btn-ghost { display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 10px; background: transparent; color: #C9CBD1;
      border: 1px solid #3A3D45; border-radius: 4px; font: 600 12px var(--bet-font); cursor: pointer; white-space: nowrap; }
    .btn-ghost:hover { background: #24262C; color: #FFF; }
    .btn-ghost:focus-visible { outline: 2px solid var(--bet-gold); outline-offset: 2px; }
    /* The event line is shown above the video; here it stays available to screen readers. */
    .meta { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; margin: 0; }
    @media (max-width: 399px) { .btn-ghost .word { display: none; } h1 { font-size: 22px; } }
  `,
  template: `
    <h1 aria-live="polite"><span class="lbl">Fight</span><span class="num">{{ match() ? '# ' + match()!.match_number : '—' }}</span></h1>
    <p class="meta">
      @if (event(); as e) { {{ e.name }} · {{ e.starts_at | date: 'd MMM y, h:mm a' }} }
      @else { {{ loading() ? 'Loading event' : 'No scheduled event' }} }
    </p>
    <span class="grow"></span>
    @if (menu(); as picker) {
      <button type="button" class="btn-ghost" [matMenuTriggerFor]="picker" aria-label="Switch event">
        <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M7 4 3 8l4 4" /><path d="M3 8h14" /><path d="m17 20 4-4-4-4" /><path d="M21 16H7" />
        </svg>
        <span class="word">Switch event</span>
      </button>
    }
    <span [class]="'pill ' + phase().tone"><span class="dot" aria-hidden="true"></span>{{ phase().label }}</span>
  `,
})
export class FightHeaderComponent {
  readonly match = input<Match | null>(null);
  readonly event = input<EventItem | null>(null);
  readonly menu = input<MatMenuPanel | null>(null);
  readonly loading = input(false);
  /** The fight's betting book; once betting has opened, the pill shows its state instead of the fight phase. */
  readonly book = input<FightBook | null>(null);

  readonly phase = computed(() => {
    const b = this.book();
    if (b?.state === 'open' && b.accepting) return { label: 'Open', tone: 'bet-open' };
    if (b?.state === 'open' || b?.state === 'closed') return { label: 'Closed', tone: 'bet-closed' };
    if (b?.state === 'settled') return { label: 'Settled', tone: 'finished' };
    const m = this.match();
    const e = this.event();
    return m ? matchPhase(m) : e ? eventPhase(e) : { label: '—', tone: 'idle' };
  });
}

import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { MatMenuModule, type MatMenuPanel } from '@angular/material/menu';

import type { EventItem, Match } from '@shared/models';
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
    :host { display: flex; flex-direction: column; gap: 16px; font-family: var(--bet-font); color: var(--bet-text); font-variant-numeric: tabular-nums; }
    .top { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 44px; }
    .pill { display: inline-flex; align-items: center; gap: 8px; height: 28px; padding: 0 12px; border: 1px solid var(--bet-line-strong); border-radius: 999px;
      font-size: 12px; font-weight: 700; letter-spacing: 0.04em; }
    .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--bet-muted); }
    .live .dot { background: var(--bet-gold); }
    .btn-ghost { display: inline-flex; align-items: center; gap: 8px; height: 44px; padding: 0 14px; background: transparent; color: inherit;
      border: 1px solid var(--bet-line-strong); border-radius: 12px; font: 600 13px var(--bet-font); cursor: pointer; }
    .btn-ghost:hover { background: var(--bet-hover); }
    .btn-ghost:focus-visible { outline: 2px solid var(--bet-gold); outline-offset: 2px; }
    h1 { margin: 0; display: flex; align-items: baseline; gap: 10px; line-height: 1; }
    .lbl { font-size: 20px; font-weight: 600; color: var(--bet-muted); }
    .num { font-size: 52px; font-weight: 800; letter-spacing: -0.04em; }
    .meta { margin: 8px 0 0; font-size: 13px; color: var(--bet-muted); font-weight: 500; overflow-wrap: anywhere; }
    @media (max-width: 479px) { .num { font-size: 44px; } }
  `,
  template: `
    <div class="top">
      <span class="pill" [class.live]="phase().tone === 'live'"><span class="dot" aria-hidden="true"></span>{{ phase().label }}</span>
      @if (menu(); as picker) {
        <button type="button" class="btn-ghost" [matMenuTriggerFor]="picker">
          <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M7 4 3 8l4 4" /><path d="M3 8h14" /><path d="m17 20 4-4-4-4" /><path d="M21 16H7" />
          </svg>
          Switch event
        </button>
      }
    </div>
    <div>
      <h1 aria-live="polite"><span class="lbl">Fight</span><span class="num">{{ match() ? '#' + match()!.match_number : '—' }}</span></h1>
      <p class="meta">
        @if (event(); as e) { {{ e.name }} · {{ e.starts_at | date: 'd MMM y, h:mm a' }} }
        @else { {{ loading() ? 'Loading event' : 'No scheduled event' }} }
      </p>
    </div>
  `,
})
export class FightHeaderComponent {
  readonly match = input<Match | null>(null);
  readonly event = input<EventItem | null>(null);
  readonly menu = input<MatMenuPanel | null>(null);
  readonly loading = input(false);

  readonly phase = computed(() => {
    const m = this.match();
    const e = this.event();
    return m ? matchPhase(m) : e ? eventPhase(e) : { label: '—', tone: 'idle' };
  });
}

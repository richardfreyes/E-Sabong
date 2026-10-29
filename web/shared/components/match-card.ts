import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import type { Match } from '@shared/models';
import { StatusChipComponent } from './primitives';

const OUTCOME_LABEL = { meron: 'Meron wins', wala: 'Wala wins', draw: 'Draw', no_contest: 'No contest' } as const;

/** Meron vs Wala competitor display with status and the official result. */
@Component({
  selector: 'app-match-card',
  imports: [StatusChipComponent, MatIconModule, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; }
    .side { border-top: 4px solid; }
    .meron { border-color: var(--app-meron); }
    .wala { border-color: var(--app-wala); }
    .winner { background: var(--mat-sys-surface-container-high); }
  `,
  template: `
    <article class="flex h-full flex-col gap-3 rounded-2xl border border-outline-variant bg-surface-container-lowest p-4">
      <header class="flex items-center justify-between gap-2">
        <div class="min-w-0">
          <p class="m-0 text-xs font-semibold uppercase tracking-wide text-on-surface-variant">Match #{{ match().match_number }}</p>
          @if (showEvent() && match().event_name) {
            <p class="m-0 truncate text-sm text-on-surface-variant">{{ match().event_name }}</p>
          }
        </div>
        <app-status-chip [status]="match().status" />
      </header>
      <div class="grid grid-cols-[1fr_auto_1fr] items-stretch gap-2">
        <div class="side meron rounded-lg p-3" [class.winner]="outcome() === 'meron'">
          <p class="m-0 text-xs font-bold tracking-widest text-meron">MERON</p>
          <p class="m-0 mt-1 break-words font-semibold">{{ match().meron_name }}</p>
          @if (match().meron_details) {
            <p class="m-0 mt-1 text-xs text-on-surface-variant">{{ match().meron_details }}</p>
          }
          @if (outcome() === 'meron') {
            <p class="m-0 mt-2 flex items-center gap-1 text-xs font-semibold"><mat-icon class="!h-4 !w-4 !text-base">emoji_events</mat-icon>Winner</p>
          }
        </div>
        <div class="flex items-center text-sm font-semibold text-on-surface-variant" aria-hidden="true">vs</div>
        <div class="side wala rounded-lg p-3" [class.winner]="outcome() === 'wala'">
          <p class="m-0 text-xs font-bold tracking-widest text-wala">WALA</p>
          <p class="m-0 mt-1 break-words font-semibold">{{ match().wala_name }}</p>
          @if (match().wala_details) {
            <p class="m-0 mt-1 text-xs text-on-surface-variant">{{ match().wala_details }}</p>
          }
          @if (outcome() === 'wala') {
            <p class="m-0 mt-2 flex items-center gap-1 text-xs font-semibold"><mat-icon class="!h-4 !w-4 !text-base">emoji_events</mat-icon>Winner</p>
          }
        </div>
      </div>
      <footer class="mt-auto flex items-center justify-between gap-2 text-xs text-on-surface-variant">
        @if (match().result; as r) {
          <span class="font-semibold text-on-surface">Official result: {{ resultLabel() }}</span>
          <span>{{ r.declared_at | date: 'short' }}@if (r.revision > 1) { · corrected }</span>
        } @else if (match().status === 'in_progress') {
          <span class="font-semibold text-on-surface">In progress{{ match().started_at ? ' · started ' + (match().started_at | date: 'shortTime') : '' }}</span>
        } @else if (match().status === 'cancelled') {
          <span>Cancelled{{ match().cancel_reason ? ': ' + match().cancel_reason : '' }}</span>
        } @else if (match().scheduled_at) {
          <span>Scheduled {{ match().scheduled_at | date: 'short' }}</span>
        } @else {
          <span>Awaiting start</span>
        }
        <ng-content />
      </footer>
    </article>
  `,
})
export class MatchCardComponent {
  readonly match = input.required<Match>();
  readonly showEvent = input(false);
  readonly outcome = computed(() => this.match().result?.outcome ?? null);
  readonly resultLabel = computed(() => {
    const o = this.outcome();
    return o ? OUTCOME_LABEL[o] : '';
  });
}

export function outcomeLabel(outcome: keyof typeof OUTCOME_LABEL): string {
  return OUTCOME_LABEL[outcome];
}

/** Accessible name for a link wrapping a match card. */
export function matchLabel(m: Match): string {
  const result = m.result ? `, ${OUTCOME_LABEL[m.result.outcome]}` : `, ${m.status.replace('_', ' ')}`;
  return `Match ${m.match_number}: ${m.meron_name} (Meron) versus ${m.wala_name} (Wala)${result}`;
}

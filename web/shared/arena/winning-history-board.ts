import {
  ChangeDetectionStrategy, Component, DestroyRef, ElementRef, afterRenderEffect,
  computed, effect, inject, input, output, signal, untracked, viewChild,
} from '@angular/core';
import { MatTooltipModule } from '@angular/material/tooltip';

import type { Match } from '@shared/models';
import { describeMatch } from './match-phase';
import { WINNING_HISTORY_ROWS, WinningHistoryOutcome, WinningHistoryResult, toWinningHistoryGrid } from './winning-history-grid';

interface RecordedFight extends WinningHistoryResult {
  match: Match;
  kind: 'meron' | 'wala' | 'draw' | 'void';
  label: string;
}

/** Project the existing official card into finalized, validated board records. */
function recordedFights(matches: readonly Match[]): RecordedFight[] {
  const records: RecordedFight[] = [];
  const outcomes: Record<string, WinningHistoryOutcome | undefined> = {
    meron: 'MERON', wala: 'WALA', draw: 'DRAW', no_contest: 'CANCELLED',
  };
  for (const match of matches) {
    const cancelled = match.status === 'cancelled';
    if (!cancelled && (match.status !== 'completed' || !match.result)) continue;
    const result = cancelled ? 'CANCELLED' : outcomes[match.result!.outcome];
    if (!result) continue;
    const revision = match.result?.revision ?? 1;
    records.push({
      fightNumber: match.match_number, result, revision, match,
      kind: result === 'CANCELLED' ? 'void' : result === 'MERON' ? 'meron' : result === 'WALA' ? 'wala' : 'draw',
      label: describeMatch(match) + (revision > 1 ? ' (corrected)' : ''),
    });
  }
  return records;
}

@Component({
  selector: 'pp-winning-history-board',
  imports: [MatTooltipModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './winning-history-board.scss',
  template: `
    @if (!cells().length) {
      <p class="empty">No official results yet.</p>
    } @else {
      <div #scroller class="board-viewport pp-scroll" tabindex="0" role="region" aria-label="Winning history, scroll horizontally for earlier results">
        <div class="board" [style.--columns]="columnCount()" [style.--rows]="rowCount" role="group" aria-label="Official results by winning streak">
          @for (cell of cells(); track cell.fightNumber) {
            <button type="button" class="bead" [class]="cell.kind" [class.corrected]="cell.revision! > 1"
                    [class.continuation]="cell.continuation" [class.fresh]="fresh().has(cell.match.id)"
                    [style.grid-column]="cell.column + 1" [style.grid-row]="cell.row + 1"
                    [attr.data-fight]="cell.fightNumber" [attr.data-column]="cell.column" [attr.data-row]="cell.row"
                    [attr.data-streak]="cell.streakId" [matTooltip]="label(cell)" [attr.aria-label]="label(cell)"
                    (click)="selected.emit(cell.match)">{{ cell.fightNumber }}</button>
          }
        </div>
      </div>
      <div class="legend" aria-hidden="true">
        <span><i class="key meron"></i>Meron</span><span><i class="key wala"></i>Wala</span>
        <span><i class="key draw"></i>Draw</span><span><i class="key void"></i>Cancelled / no contest</span>
        <span><i class="continuation-key"></i>Streak continues</span>
        @if (hasCorrections()) { <span><i class="correction-key"></i>Corrected</span> }
      </div>
    }
  `,
})
export class WinningHistoryBoardComponent {
  readonly matches = input.required<Match[]>();
  readonly eventId = input<string | null>(null);
  readonly reduceMotion = input(false);
  readonly selected = output<Match>();
  readonly rowCount = WINNING_HISTORY_ROWS;
  readonly cells = computed(() => toWinningHistoryGrid(recordedFights(this.matches())));
  readonly columnCount = computed(() => (this.cells().at(-1)?.column ?? -1) + 1);
  readonly hasCorrections = computed(() => this.cells().some(cell => cell.revision! > 1));
  readonly fresh = signal(new Set<string>());

  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');
  private readonly viewportWidth = signal(0);
  private known = new Map<string, string>();
  private knownEvent: string | null | undefined;
  private seeded = false;
  private animationTimer: ReturnType<typeof setTimeout> | undefined;
  private lastScrollKey = '';

  readonly label = (cell: { label: string; continuation: boolean }) =>
    cell.label + (cell.continuation ? ', winning streak continued from previous column' : '');

  constructor() {
    const destroy = inject(DestroyRef);
    destroy.onDestroy(() => clearTimeout(this.animationTimer));

    effect(() => {
      const event = this.eventId() ?? this.matches()[0]?.event_id ?? null;
      const cells = this.cells();
      untracked(() => {
        if (event !== this.knownEvent) {
          this.knownEvent = event;
          this.seeded = false;
          this.known.clear();
          this.fresh.set(new Set());
          clearTimeout(this.animationTimer);
        }
        const changed = cells.filter(cell => this.known.get(cell.match.id) !== `${cell.result}:${cell.revision}`);
        this.known = new Map(cells.map(cell => [cell.match.id, `${cell.result}:${cell.revision}`]));
        if (this.seeded && changed.length) {
          clearTimeout(this.animationTimer);
          this.fresh.set(new Set([...this.fresh(), ...changed.map(cell => cell.match.id)]));
          this.animationTimer = setTimeout(() => this.fresh.set(new Set()), 900);
        }
        this.seeded = true;
      });
    });

    afterRenderEffect(onCleanup => {
      const el = this.scroller()?.nativeElement;
      if (!el || typeof ResizeObserver === 'undefined') return;
      const observer = new ResizeObserver(() => this.viewportWidth.set(el.clientWidth));
      observer.observe(el);
      onCleanup(() => observer.disconnect());
    });

    afterRenderEffect(() => {
      const el = this.scroller()?.nativeElement;
      const cells = this.cells();
      const width = this.viewportWidth();
      const key = `${this.eventId()}:${width}:` + cells.map(cell => `${cell.fightNumber}:${cell.result}:${cell.revision}:${cell.column}:${cell.row}`).join('|');
      if (!el || !cells.length || key === this.lastScrollKey) return;
      const first = !this.lastScrollKey;
      this.lastScrollKey = key;
      const reduced = this.reduceMotion() || (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
      // scrollWidth clamps to the last column, even when a correction reflows earlier streaks.
      el.scrollTo({ left: el.scrollWidth, behavior: first || reduced ? 'auto' : 'smooth' });
    });
  }
}

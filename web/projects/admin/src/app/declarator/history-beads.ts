import { ChangeDetectionStrategy, Component, ElementRef, afterRenderEffect, computed, input, output, viewChild } from '@angular/core';

import { WINNING_HISTORY_ROWS, WinningHistoryOutcome, toWinningHistoryGrid } from '@shared/arena';
import type { Match } from '@shared/models';

type Kind = 'meron' | 'wala' | 'draw' | 'void';
interface Bead { key: string; column: number; row: number; kind: Kind | 'empty'; fight: number | null; match: Match | null; label: string }

const OUTCOME: Record<string, WinningHistoryOutcome> = { meron: 'MERON', wala: 'WALA', draw: 'DRAW', no_contest: 'CANCELLED' };
const KIND: Record<WinningHistoryOutcome, Kind> = { MERON: 'meron', WALA: 'wala', DRAW: 'draw', CANCELLED: 'void' };
const LABEL: Record<Kind, string> = { meron: 'Meron', wala: 'Wala', draw: 'Draw', void: 'Cancelled / no contest' };
/** Columns always drawn, so the board reads as a full grid of empty beads before results arrive. */
export const MIN_BEAD_COLUMNS = 10;

/**
 * Declarator winning history: the shared streak layout (red Meron, blue Wala, green Draw, neutral for
 * cancelled / no contest) on a full grid of empty beads, scrolled to the latest result. Official
 * results only; it re-renders whenever the monitor snapshot (realtime) changes.
 */
@Component({
  selector: 'app-history-beads',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div #scroller class="beads-viewport" tabindex="0" role="region" aria-label="Winning history, newest on the right">
      <div class="beads" [style.--columns]="columns()" [style.--rows]="rows" role="list">
        @for (b of beads(); track b.key) {
          @if (b.match) {
            <button type="button" role="listitem" class="bead" [class]="'bead ' + b.kind" [style.grid-column]="b.column + 1" [style.grid-row]="b.row + 1"
              [attr.data-fight]="b.fight" [attr.aria-label]="b.label" [title]="b.label" (click)="selected.emit(b.match)">{{ b.fight }}</button>
          } @else {
            <span role="listitem" class="bead empty" [style.grid-column]="b.column + 1" [style.grid-row]="b.row + 1" aria-hidden="true"></span>
          }
        }
      </div>
    </div>
  `,
  styles: `
    :host { display: block; min-width: 0; }
    .beads-viewport { overflow-x: auto; padding: 2px 0 6px; scroll-snap-type: x mandatory; scrollbar-width: thin; }
    .beads { --bead: 32px; display: grid; grid-template-columns: repeat(var(--columns), var(--bead)); grid-template-rows: repeat(var(--rows), var(--bead));
      gap: 6px; width: max-content; margin-inline: auto; }
    .bead { width: var(--bead); height: var(--bead); border-radius: 50%; border: 0; padding: 0; display: grid; place-items: center; scroll-snap-align: start;
      font: inherit; font-size: .62rem; font-weight: 700; color: white; font-variant-numeric: tabular-nums; letter-spacing: -.02em; }
    button.bead { cursor: pointer; box-shadow: inset 0 -2px 0 rgba(0,0,0,.18); transition: transform .12s; }
    button.bead:hover { transform: scale(1.08); }
    button.bead:focus-visible { outline: 2px solid white; outline-offset: 2px; }
    .bead.empty { background: var(--bead-empty, rgba(226, 232, 240, .1)); box-shadow: inset 0 0 0 1px var(--bead-empty-line, rgba(226, 232, 240, .14)); }
    .bead.meron { background: #e5243f; } .bead.wala { background: #2563eb; } .bead.draw { background: #16a34a; }
    .bead.void { background: #6b7280; }
    @media (max-width: 599px) { .beads { --bead: 28px; gap: 5px; } .bead { font-size: .56rem; } }
  `,
})
export class HistoryBeadsComponent {
  readonly matches = input.required<Match[]>();
  readonly selected = output<Match>();
  readonly rows = WINNING_HISTORY_ROWS;
  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  private readonly cells = computed(() => {
    const results = [];
    for (const match of this.matches()) {
      const cancelled = match.status === 'cancelled';
      if (!cancelled && (match.status !== 'completed' || !match.result)) continue;
      const result = cancelled ? 'CANCELLED' : OUTCOME[match.result!.outcome];
      if (result) results.push({ fightNumber: match.match_number, result, revision: match.result?.revision ?? 1, match });
    }
    return toWinningHistoryGrid(results);
  });
  readonly columns = computed(() => Math.max(MIN_BEAD_COLUMNS, (this.cells().at(-1)?.column ?? -1) + 2));
  readonly beads = computed<Bead[]>(() => {
    const taken = new Map(this.cells().map(c => [`${c.column}:${c.row}`, c]));
    const out: Bead[] = [];
    for (let column = 0; column < this.columns(); column++) {
      for (let row = 0; row < this.rows; row++) {
        const cell = taken.get(`${column}:${row}`);
        const kind = cell ? KIND[cell.result] : 'empty';
        out.push({
          key: `${column}:${row}`, column, row, kind, fight: cell?.fightNumber ?? null, match: cell?.match ?? null,
          label: cell ? `Fight #${cell.fightNumber}: ${LABEL[kind as Kind]}${(cell.revision ?? 1) > 1 ? ' (corrected)' : ''}` : '',
        });
      }
    }
    return out;
  });

  constructor() {
    // Keep the newest results in view as they arrive.
    afterRenderEffect(() => {
      this.cells();
      const el = this.scroller()?.nativeElement;
      if (el) el.scrollLeft = el.scrollWidth;
    });
  }
}

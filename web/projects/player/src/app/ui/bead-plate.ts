import {
  ChangeDetectionStrategy, Component, ElementRef, afterRenderEffect, computed, input, output, viewChild,
} from '@angular/core';

import type { Match } from '@shared/models';
import { toCents } from '@shared/pipes';
import { beadKind, describeMatch, isSettled } from '../core/match-phase';

export const BEAD_PLATE_ROWS = 7;

export interface PlateBead {
  match: Match;
  kind: 'meron' | 'wala' | 'draw' | 'void';
  column: number;
  row: number;
  label: string;
}

export interface SidePools {
  meron: string;
  wala: string;
}

/**
 * The winning side's stake against the other side.
 * The higher total is L (llamado); the lower total is D (dehado). Equal or unknown totals are unmarked.
 */
export function stakeMark(meron: string, wala: string, winner: 'meron' | 'wala'): 'L' | 'D' | null {
  const meronCents = toCents(meron);
  const walaCents = toCents(wala);
  if (meronCents === null || walaCents === null || meronCents === walaCents) return null;
  const winnerStake = winner === 'meron' ? meronCents : walaCents;
  const otherStake = winner === 'meron' ? walaCents : meronCents;
  return winnerStake > otherStake ? 'L' : 'D';
}

/** Every decided fight in fight-number order, filled top to bottom, then the next column. */
export function toBeadPlate(matches: readonly Match[], rows = BEAD_PLATE_ROWS): PlateBead[] {
  return matches
    .filter(isSettled)
    .slice()
    .sort((a, b) => a.match_number - b.match_number)
    .map((match, i) => {
      const kind = beadKind(match);
      const corrected = (match.result?.revision ?? 1) > 1;
      return {
        match,
        kind: kind === 'meron' || kind === 'wala' || kind === 'draw' ? kind : 'void',
        column: Math.floor(i / rows),
        row: i % rows,
        label: describeMatch(match) + (corrected ? ' (corrected)' : ''),
      };
    });
}

/**
 * The bead plate: one filled, numbered bead per official result (red MERON, blue WALA, green DRAW,
 * grey cancelled / no contest). It reads the arena's match list only; selecting a bead opens the fight.
 */
@Component({
  selector: 'pp-bead-plate',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: flex; flex: 1; flex-direction: column; min-width: 0; min-height: 0; }
    .viewport { --cell: 48px; --rows: 7; flex: 1; min-height: calc(var(--rows) * var(--cell)); overflow-x: auto; overscroll-behavior-x: contain; scrollbar-width: thin; scrollbar-color: #3A3D45 transparent; background: #000; }
    .viewport:focus-visible { outline: 2px solid #FFD23F; outline-offset: -2px; }
    .plate { display: grid; grid-template-columns: repeat(var(--columns), var(--cell)); grid-template-rows: repeat(var(--rows), var(--cell));
      grid-auto-flow: column; width: max(100%, calc(var(--columns) * var(--cell))); min-height: calc(var(--rows) * var(--cell));
      background-image: linear-gradient(to right, #2C2C2C 1px, transparent 1px), linear-gradient(to bottom, #2C2C2C 1px, transparent 1px);
      background-size: var(--cell) var(--cell); }
    .bead { position: relative; display: grid; place-items: center; width: calc(var(--cell) - 4px); height: calc(var(--cell) - 4px); margin: 2px; padding: 0;
      border: 0; border-radius: 50%; color: #FFF; font: 700 14px/1 'Noto Sans', var(--bet-font, system-ui), sans-serif; font-variant-numeric: tabular-nums; cursor: pointer;
      box-shadow: inset 0 -2px 0 rgb(0 0 0 / 0.28); transition: transform 120ms ease; }
    .bead.meron { background: #C62828; }
    .bead.wala { background: #3862F6; }
    .bead.draw { background: #5BC561; }
    .bead.void { background: #424242; color: #FFF; }
    .mark { position: absolute; z-index: 1; display: grid; place-items: center; width: 14px; height: 14px; border-radius: 50%;
      border: 1px solid rgb(255 255 255 / 0.45); color: #FFF; font: 700 8px/1 'Noto Sans', var(--bet-font, system-ui), sans-serif; pointer-events: none; }
    .mark.l { top: -1px; left: -1px; }
    .mark.d { right: -1px; bottom: -1px; }
    .bead.meron .mark { background: #EF5350; }
    .bead.wala .mark { background: #42A5F5; }
    .bead.corrected::after { content: ''; position: absolute; top: 0; right: 0; width: 7px; height: 7px; border-radius: 50%; background: #FFF; box-shadow: 0 0 0 1px #0E0F12; }
    .bead:hover { transform: scale(1.1); z-index: 1; }
    .bead:focus-visible { outline: 2px solid #FFD23F; outline-offset: 1px; z-index: 1; }
    .empty { margin: 0; padding: 14px 12px; color: #9E9E9E; font-size: 13px; background: #000; }
    @media (max-width: 479px) { .viewport { --cell: 40px; } .bead { font-size: 12px; } }
  `,
  template: `
    @if (!beads().length) {
      <p class="empty">No official results yet.</p>
    } @else {
      <div #scroller class="viewport" tabindex="0" role="region" aria-label="Results in fight order, scroll horizontally for earlier fights">
        <div class="plate" [style.--columns]="columns()" [style.--rows]="rows" role="group" aria-label="Official results in fight order">
          @for (b of beads(); track b.match.id) {
            <button type="button" [class]="'bead ' + b.kind" [class.corrected]="(b.match.result?.revision ?? 1) > 1"
                    [style.grid-column]="b.column + 1" [style.grid-row]="b.row + 1" [attr.data-fight]="b.match.match_number"
                    [attr.aria-label]="markedLabel(b)" [title]="markedLabel(b)" (click)="selected.emit(b.match)">{{ b.match.match_number }}
              @if (markOf(b); as mark) { <span class="mark" [class]="mark === 'L' ? 'l' : 'd'">{{ mark }}</span> }
            </button>
          }
        </div>
      </div>
    }
  `,
})
export class BeadPlateComponent {
  readonly matches = input.required<Match[]>();
  /** All-player MERON and WALA totals keyed by match id. Missing fights stay unmarked. */
  readonly pools = input<Readonly<Record<string, SidePools>>>({});
  readonly selected = output<Match>();
  readonly rows = BEAD_PLATE_ROWS;
  readonly beads = computed(() => toBeadPlate(this.matches(), this.rows));
  readonly columns = computed(() => Math.max(1, (this.beads().at(-1)?.column ?? 0) + 1));

  markOf(bead: PlateBead): 'L' | 'D' | null {
    if (bead.kind !== 'meron' && bead.kind !== 'wala') return null;
    const pool = this.pools()[bead.match.id];
    return pool ? stakeMark(pool.meron, pool.wala, bead.kind) : null;
  }

  markedLabel(bead: PlateBead): string {
    const mark = this.markOf(bead);
    if (mark === 'L') return `${bead.label}, llamado, higher stake`;
    if (mark === 'D') return `${bead.label}, dehado, lower stake`;
    return bead.label;
  }

  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  constructor() {
    // The newest fights are on the right: keep them in view as results arrive.
    afterRenderEffect(() => {
      const el = this.scroller()?.nativeElement;
      if (el && this.beads().length) el.scrollLeft = el.scrollWidth;
    });
  }
}

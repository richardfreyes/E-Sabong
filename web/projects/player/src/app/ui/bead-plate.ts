import {
  ChangeDetectionStrategy, Component, ElementRef, afterRenderEffect, computed, input, output, viewChild,
} from '@angular/core';

import type { Match } from '@shared/models';
import { beadKind, describeMatch, isSettled } from '../core/match-phase';

export const BEAD_PLATE_ROWS = 7;

export interface PlateBead {
  match: Match;
  kind: 'meron' | 'wala' | 'draw' | 'void';
  column: number;
  row: number;
  label: string;
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
    :host { display: block; min-width: 0; }
    .viewport { --cell: 36px; overflow-x: auto; overscroll-behavior-x: contain; scrollbar-width: thin; scrollbar-color: #3A3D45 transparent; background: #000; }
    .viewport:focus-visible { outline: 2px solid #FFD23F; outline-offset: -2px; }
    .plate { display: grid; grid-template-columns: repeat(var(--columns), var(--cell)); grid-template-rows: repeat(var(--rows), var(--cell));
      grid-auto-flow: column; width: max-content; min-width: 100%;
      background-image: linear-gradient(to right, #2C2C2C 1px, transparent 1px), linear-gradient(to bottom, #2C2C2C 1px, transparent 1px);
      background-size: var(--cell) var(--cell); }
    .bead { position: relative; display: grid; place-items: center; width: calc(var(--cell) - 2px); height: calc(var(--cell) - 2px); margin: 1px; padding: 0;
      border: 0; border-radius: 50%; color: #FFF; font: 500 13px/1 'Noto Sans', var(--bet-font, system-ui), sans-serif; font-variant-numeric: tabular-nums; cursor: pointer;
      box-shadow: inset 0 -2px 0 rgb(0 0 0 / 0.25); transition: transform 120ms ease; }
    .bead.meron { background: #C62828; }
    .bead.wala { background: #2962FF; }
    .bead.draw { background: #43A047; }
    .bead.void { background: #424242; color: #FFF; }
    .bead.corrected::after { content: ''; position: absolute; top: 0; right: 0; width: 7px; height: 7px; border-radius: 50%; background: #FFF; box-shadow: 0 0 0 1px #0E0F12; }
    .bead:hover { transform: scale(1.1); z-index: 1; }
    .bead:focus-visible { outline: 2px solid #FFD23F; outline-offset: 1px; z-index: 1; }
    .empty { margin: 0; padding: 14px 12px; color: #9E9E9E; font-size: 13px; background: #000; }
    @media (max-width: 479px) { .viewport { --cell: 34px; } }
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
                    [attr.aria-label]="b.label" [title]="b.label" (click)="selected.emit(b.match)">{{ b.match.match_number }}</button>
          }
        </div>
      </div>
    }
  `,
})
export class BeadPlateComponent {
  readonly matches = input.required<Match[]>();
  readonly selected = output<Match>();
  readonly rows = BEAD_PLATE_ROWS;
  readonly beads = computed(() => toBeadPlate(this.matches(), this.rows));
  readonly columns = computed(() => Math.max(1, (this.beads().at(-1)?.column ?? 0) + 1));

  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  constructor() {
    // The newest fights are on the right: keep them in view as results arrive.
    afterRenderEffect(() => {
      const el = this.scroller()?.nativeElement;
      if (el && this.beads().length) el.scrollLeft = el.scrollWidth;
    });
  }
}

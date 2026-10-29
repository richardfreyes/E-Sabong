import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { DerbyStats } from '../core/match-phase';

interface Segment {
  key: 'meron' | 'draw' | 'void' | 'wala';
  label: string;
  count: number;
  pct: number;
}

/**
 * Win split for a derby: one tug bar (Meron pulling from the left, Wala from
 * the right, draws and cancellations between) and compact counters.
 */
@Component({
  selector: 'pp-derby-record',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; }
    .tug { display: flex; gap: 2px; height: 0.25rem; border-radius: 999px; overflow: hidden; background: var(--pp-pit-raised); }
    .seg { min-width: 0; transition: flex-basis 600ms ease; }
    .seg.meron { background: var(--pp-meron); }
    .seg.wala { background: var(--pp-wala); }
    .seg.draw { background: var(--pp-draw); }
    .seg.void { background: var(--pp-void); }
    .counts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.5rem; margin: 0.6rem 0 0; }
    .count { border-radius: 0.5rem; background: var(--pp-pit-raised); padding: 0.55rem 0.7rem; min-width: 0; }
    .count dt { display: flex; align-items: center; gap: 0.4rem; color: var(--pp-ash); font-size: 0.7rem; white-space: nowrap; }
    .count dd { margin: 0.3rem 0 0; display: flex; align-items: baseline; gap: 0.4rem; justify-content: space-between; color: var(--pp-chalk); }
    .n { font-size: 1.5rem; }
    .p { color: var(--pp-ash); font-size: 0.7rem; font-variant-numeric: tabular-nums; }
    .mark { width: 0.55rem; height: 0.55rem; border-radius: 50%; flex: none; }
    .mark.meron { background: var(--pp-meron); } .mark.wala { background: var(--pp-wala); }
    .mark.draw { background: var(--pp-draw); } .mark.void { background: var(--pp-void); }
    @media (max-width: 639px) {
      .counts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  `,
  template: `
    <div class="tug" role="img" [attr.aria-label]="summary()">
      @for (s of segments(); track s.key) {
        @if (s.count) { <span class="seg" [class]="s.key" [style.flex-basis.%]="s.pct" [style.flex-grow]="0"></span> }
      }
    </div>
    <dl class="counts">
      @for (s of segments(); track s.key) {
        <div class="count">
          <dt><i class="mark" [class]="s.key" aria-hidden="true"></i>{{ s.label }}</dt>
          <dd><span class="pp-num n">{{ s.count }}</span><span class="p">{{ s.pct }}%</span></dd>
        </div>
      }
    </dl>
  `,
})
export class DerbyRecordComponent {
  readonly stats = input.required<DerbyStats>();

  readonly segments = computed<Segment[]>(() => {
    const s = this.stats();
    return [
      { key: 'meron', label: 'Meron', count: s.meron, pct: s.pct(s.meron) },
      { key: 'draw', label: 'Draw', count: s.draw, pct: s.pct(s.draw) },
      { key: 'void', label: 'Cancelled', count: s.voided, pct: s.pct(s.voided) },
      { key: 'wala', label: 'Wala', count: s.wala, pct: s.pct(s.wala) },
    ];
  });

  readonly summary = computed(() => {
    const s = this.stats();
    if (!s.total) return 'No fights decided yet';
    return `${s.total} fights decided: Meron ${s.meron}, Wala ${s.wala}, draws ${s.draw}, cancelled ${s.voided}`;
  });
}

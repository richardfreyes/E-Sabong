import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { Match } from '@shared/models';
import { OUTCOME_TEXT } from '../core/match-phase';

interface Step {
  label: string;
  at: string | null;
  done: boolean;
  note?: string | null;
  tone?: 'meron' | 'wala' | 'draw' | 'void';
}

/** The sequence a fight goes through, from the card to the official result. */
@Component({
  selector: 'pp-match-timeline',
  imports: [DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
    li { position: relative; display: grid; grid-template-columns: 1.25rem 1fr auto; gap: 0.6rem; padding-bottom: 0.9rem; }
    li:last-child { padding-bottom: 0; }
    li:not(:last-child)::before { content: ''; position: absolute; left: 0.56rem; top: 1.05rem; bottom: 0.1rem; width: 2px; background: var(--pp-rope); }
    li.done:not(:last-child)::before { background: var(--border); }
    .node { margin-top: 0.2rem; width: 0.85rem; height: 0.85rem; border-radius: 999px; border: 2px solid var(--border); background: var(--pp-graphite); justify-self: center; }
    .done .node { background: var(--pp-chalk); border-color: var(--pp-chalk); }
    .meron .node { background: var(--pp-meron); border-color: var(--pp-meron); }
    .wala .node { background: var(--pp-wala); border-color: var(--pp-wala); }
    .draw .node { background: var(--pp-draw); border-color: var(--pp-draw); }
    .void .node { background: var(--pp-void); border-color: var(--pp-void); }
    .label { color: var(--pp-ash); font-size: 0.875rem; }
    .done .label { color: var(--pp-chalk); }
    .note { display: block; color: var(--pp-ash); font-size: 0.8rem; margin-top: 0.15rem; }
    time { color: var(--pp-ash); font-size: 0.8rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
  `,
  template: `
    <ol aria-label="Fight timeline">
      @for (s of steps(); track s.label) {
        <li [class.done]="s.done" [class]="s.tone ?? ''">
          <span class="node" aria-hidden="true"></span>
          <span class="label">{{ s.label }}@if (s.note) {<span class="note">{{ s.note }}</span>}</span>
          <time>{{ s.at ? (s.at | date: 'h:mm a') : s.done ? '' : 'Pending' }}</time>
        </li>
      }
    </ol>
  `,
})
export class MatchTimelineComponent {
  readonly match = input.required<Match>();

  readonly steps = computed<Step[]>(() => {
    const m = this.match();
    const steps: Step[] = [{ label: 'On the fight card', at: m.scheduled_at, done: true }];
    if (m.status === 'cancelled') {
      if (m.started_at) steps.push({ label: 'Fight started', at: m.started_at, done: true });
      steps.push({ label: 'Fight cancelled', at: m.ended_at, done: true, note: m.cancel_reason, tone: 'void' });
      return steps;
    }
    steps.push({ label: 'Fight started', at: m.started_at, done: !!m.started_at });
    const r = m.result;
    if (r) {
      const tone = r.outcome === 'no_contest' ? 'void' : r.outcome;
      steps.push({
        label: r.revision > 1 ? `Result corrected: ${OUTCOME_TEXT[r.outcome]}` : `Official result: ${OUTCOME_TEXT[r.outcome]}`,
        at: r.declared_at,
        done: true,
        note: r.notes,
        tone,
      });
    } else {
      steps.push({ label: 'Official result', at: null, done: false });
    }
    return steps;
  });
}

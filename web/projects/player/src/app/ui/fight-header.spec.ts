import { TestBed } from '@angular/core/testing';

import type { EventItem, FightBook, Match } from '@shared/models';
import { FightHeaderComponent } from './fight-header';

const EVENT = { id: 'e1', name: 'Grand Derby Demo', status: 'live', starts_at: '2026-10-28T07:47:00Z' } as EventItem;
const FIGHT = { id: 'm1', match_number: 3, status: 'in_progress' } as Match;

describe('FightHeaderComponent', () => {
  function setup(inputs: Record<string, unknown>) {
    const fixture = TestBed.createComponent(FightHeaderComponent);
    for (const [k, v] of Object.entries(inputs)) fixture.componentRef.setInput(k, v);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    return { el, text: el.textContent!.replace(/\s+/g, ' ') };
  }

  it('shows the fight number, event line and phase from the arena data', () => {
    const { el, text } = setup({ match: FIGHT, event: EVENT });
    expect(el.querySelector('h1')!.textContent).toBe('Fight# 3');
    expect(text).toContain('Grand Derby Demo · 28 Oct 2026');
    expect(el.querySelector('.pill')!.textContent!.trim()).toBe('Live');
    expect(el.querySelector('.pill')!.classList).toContain('live');
    expect(el.querySelector('button')).toBeNull(); // no picker: a single event
  });

  it('labels other phases and the empty state without inventing a fight', () => {
    expect(setup({ match: { ...FIGHT, status: 'completed' }, event: EVENT }).el.querySelector('.pill')!.textContent!.trim()).toBe('Finished');
    const empty = setup({ loading: true });
    expect(empty.el.querySelector('h1')!.textContent).toBe('Fight—');
    expect(empty.text).toContain('Loading event');
  });

  it('shows the betting state once betting has opened, as on the reference board', () => {
    const book = (state: string | null, accepting: boolean) => ({ state, accepting }) as unknown as FightBook;
    const pill = (b: FightBook) => setup({ match: FIGHT, event: EVENT, book: b }).el.querySelector('.pill')!;
    expect(pill(book('open', true)).textContent!.trim()).toBe('Open');
    expect(pill(book('open', true)).classList).toContain('bet-open');
    expect(pill(book('open', false)).textContent!.trim()).toBe('Closed');
    expect(pill(book('closed', false)).classList).toContain('bet-closed');
    expect(pill(book('settled', false)).textContent!.trim()).toBe('Settled');
    expect(pill(book(null, false)).textContent!.trim()).toBe('Live'); // never opened: the fight phase
  });
});

import { Component, inject, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Subject, filter, map, of } from 'rxjs';
import type { EventItem, Match, MatchOutcome, RealtimeMessage } from '@shared/models';
import { PortalApi, RealtimeService } from '@shared/services';
import { ArenaStore } from '../core/arena.store';
import { PlayerPrefs } from '../core/player-prefs';
import { WinningHistoryBoardComponent } from './winning-history-board';

const fight = (n: number, outcome: MatchOutcome | null = null): Match => ({
  id: `match-${n}`, event_id: 'event-1', event_name: 'Event', match_number: n,
  meron_name: 'Entry name', wala_name: 'Other entry', meron_details: null, wala_details: null,
  scheduled_at: null, started_at: null, ended_at: null, cancel_reason: null,
  status: outcome ? 'completed' : 'in_progress',
  result: outcome ? { outcome, revision: 1, declared_at: '2026-01-01T00:00:00Z', notes: null } : null,
});

@Component({
  imports: [WinningHistoryBoardComponent],
  template: `<pp-winning-history-board [matches]="store.matches()" [eventId]="store.eventId()" />`,
})
class LiveHistoryHost {
  readonly store = inject(ArenaStore);
  constructor() { this.store.init(); }
}

describe('WinningHistoryBoardComponent', () => {
  const scrollTo = vi.fn();
  beforeEach(() => {
    vi.useFakeTimers();
    scrollTo.mockClear();
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: scrollTo });
  });
  afterEach(() => { TestBed.resetTestingModule(); vi.useRealTimers(); });

  function setup(matches: Match[]) {
    const fixture = TestBed.createComponent(WinningHistoryBoardComponent);
    fixture.componentRef.setInput('eventId', 'event-1');
    fixture.componentRef.setInput('matches', matches);
    fixture.detectChanges();
    return { fixture, board: fixture.componentInstance, buttons: () => Array.from(fixture.nativeElement.querySelectorAll('.bead')) as HTMLButtonElement[] };
  }

  it('renders only finalized official outcomes, with neutral outcomes and usable detail buttons', () => {
    const cancelled = { ...fight(104), status: 'cancelled' } as Match;
    const { fixture, buttons } = setup([fight(101, 'meron'), fight(102, 'wala'), fight(103, 'draw'), cancelled, fight(105, 'no_contest'), fight(106)]);
    expect(buttons().map(b => b.textContent?.trim())).toEqual(['101', '102', '103', '104', '105']);
    expect(buttons().map(b => ['meron', 'wala', 'draw', 'void'].find(kind => b.classList.contains(kind)))).toEqual(['meron', 'wala', 'draw', 'void', 'void']);
    const selected = vi.fn();
    fixture.componentInstance.selected.subscribe(selected);
    buttons()[0].click();
    expect(selected).toHaveBeenCalledWith(expect.objectContaining({ match_number: 101 }));
  });

  it('deduplicates incoming history and reflows correctly after an official correction', () => {
    const first = fight(101, 'meron');
    const second = fight(102, 'meron');
    const { fixture, board, buttons } = setup([first, second, second]);
    expect(buttons()).toHaveLength(2);
    expect(board.cells().map(cell => [cell.column, cell.row])).toEqual([[0, 0], [0, 1]]);
    fixture.componentRef.setInput('matches', [first, second, { ...second, result: { ...second.result!, outcome: 'wala', revision: 2 } }]);
    fixture.detectChanges();
    expect(board.cells().map(cell => [cell.column, cell.row])).toEqual([[0, 0], [1, 0]]);
    expect(buttons()[1].classList.contains('corrected')).toBe(true);
  });

  it('animates the first live result after an empty history, scrolls, and does not replay a duplicate', () => {
    const { fixture, board, buttons } = setup([]);
    fixture.componentRef.setInput('matches', [fight(101, 'meron')]);
    fixture.detectChanges();
    expect(board.fresh().has('match-101')).toBe(true);
    expect(scrollTo).toHaveBeenCalled();
    vi.advanceTimersByTime(901);
    fixture.detectChanges();
    const calls = scrollTo.mock.calls.length;
    fixture.componentRef.setInput('matches', [fight(101, 'meron'), fight(101, 'meron')]);
    fixture.detectChanges();
    expect(board.fresh().size).toBe(0);
    expect(buttons()).toHaveLength(1);
    expect(scrollTo.mock.calls.length).toBe(calls);
  });

  it('keeps six grid rows and marks overflow as a continuation', () => {
    const { board, buttons } = setup(Array.from({ length: 8 }, (_, index) => fight(index + 101, 'meron')));
    expect(board.rowCount).toBe(6);
    expect(buttons()[6].dataset['row']).toBe('0');
    expect(buttons()[6].dataset['column']).toBe('1');
    expect(buttons()[6].getAttribute('aria-label')).toContain('winning streak continued');
    expect(buttons()[7].dataset['row']).toBe('1');
  });

  it('renders store WebSocket updates without recreating the component or losing previous results', () => {
    const messages = new Subject<RealtimeMessage>();
    const event = { id: 'event-1', name: 'Event', status: 'live', starts_at: '2026-01-01T00:00:00Z' } as EventItem;
    const card = [fight(101, 'meron'), fight(102), { ...fight(103), status: 'scheduled' } as Match];
    TestBed.configureTestingModule({ providers: [ArenaStore,
      { provide: PortalApi, useValue: { events: () => of({ items: [event] }), event: () => of(event), eventMatches: () => of(card) } },
      { provide: PlayerPrefs, useValue: { announcements: () => false, liveUpdates: () => true } },
      { provide: RealtimeService, useValue: { state: signal('open'), on: (...types: string[]) => messages.pipe(filter(m => types.includes(m.type)), map(m => m.data)) } },
    ] });
    const fixture = TestBed.createComponent(LiveHistoryHost);
    fixture.detectChanges();
    const board = fixture.debugElement.query(By.directive(WinningHistoryBoardComponent)).componentInstance as WinningHistoryBoardComponent;
    const firstButton = fixture.nativeElement.querySelector('[data-fight="101"]');
    const result = { type: 'match.result_declared', data: { ...card[1], status: 'completed', result: { outcome: 'meron', revision: 1 } } };
    messages.next(result);
    fixture.detectChanges();
    expect(board.cells().map(cell => [cell.fightNumber, cell.column, cell.row])).toEqual([[101, 0, 0], [102, 0, 1]]);
    expect(fixture.nativeElement.querySelector('[data-fight="101"]')).toBe(firstButton);
    expect(board.fresh().has('match-102')).toBe(true);
    messages.next(result);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('.bead')).toHaveLength(2);
    messages.next({ type: 'match.result_corrected', data: { ...result.data, result: { outcome: 'wala', revision: 2 } } });
    fixture.detectChanges();
    expect(board.cells().map(cell => [cell.fightNumber, cell.column, cell.row])).toEqual([[101, 0, 0], [102, 1, 0]]);
    // A delayed API snapshot and old WebSocket replay cannot undo revision 2.
    vi.advanceTimersByTime(450);
    messages.next(result);
    fixture.detectChanges();
    expect(board.cells()[1].result).toBe('WALA');
    expect(fixture.debugElement.query(By.directive(WinningHistoryBoardComponent)).componentInstance).toBe(board);
  });
});

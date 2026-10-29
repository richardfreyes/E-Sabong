import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { Observable, Subject, filter, map, of } from 'rxjs';
import type { EventItem, Match, RealtimeMessage } from '@shared/models';
import { PortalApi, RealtimeService } from '@shared/services';
import { ArenaStore } from './arena.store';
import { PlayerPrefs } from './player-prefs';

const event: EventItem = {
  id: 'event-1', name: 'Event', venue: null, description: null, starts_at: '2026-01-01T00:00:00Z', ends_at: null,
  status: 'live', created_at: '2026-01-01T00:00:00Z', match_count: 2, completed_match_count: 0,
};
const match = (n: number, status: Match['status']): Match => ({
  id: `match-${n}`, event_id: event.id, event_name: event.name, match_number: n,
  meron_name: 'Private entry name', wala_name: 'Other entry', meron_details: 'Entry A', wala_details: 'Entry B',
  scheduled_at: null, started_at: null, ended_at: null, status, cancel_reason: null, result: null,
});

describe('ArenaStore real-time match updates', () => {
  function setup(announcements = false) {
    const messages = new Subject<RealtimeMessage>();
    const matches = [match(135, 'in_progress'), match(136, 'scheduled')];
    TestBed.configureTestingModule({ providers: [ArenaStore,
      { provide: PortalApi, useValue: { events: () => of({ items: [event] }), event: () => of(event), eventMatches: () => of(matches) } },
      { provide: PlayerPrefs, useValue: { announcements: () => announcements, liveUpdates: () => true } },
      { provide: RealtimeService, useValue: {
        state: signal('open'),
        on: <T>(...types: string[]): Observable<T> => messages.pipe(filter(m => types.includes(m.type)), map(m => m.data as T)),
      } },
    ] });
    const store = TestBed.inject(ArenaStore);
    store.init();
    return { store, messages, matches };
  }

  it('advances the fight number and history immediately on a finalized result', () => {
    const { store, messages, matches } = setup();
    expect(store.current()?.match_number).toBe(135);
    messages.next({ type: 'match.result_declared', data: { ...matches[0], status: 'completed', result: { outcome: 'meron', revision: 1 } } });
    expect(store.current()?.match_number).toBe(136);
    expect(store.previous()?.match_number).toBe(135);
    expect(store.stats().meron).toBe(1);
    expect(store.stats().total).toBe(1);
  });

  it('corrects the official statistics without duplicating a result', () => {
    const { store, messages, matches } = setup();
    for (const [outcome, revision] of [['meron', 1], ['wala', 2]] as const) {
      messages.next({ type: revision === 1 ? 'match.result_declared' : 'match.result_corrected', data: { ...matches[0], status: 'completed', result: { outcome, revision } } });
    }
    expect(store.stats().total).toBe(1);
    expect(store.stats().meron).toBe(0);
    expect(store.stats().wala).toBe(1);
    expect(store.matches()[0].result?.revision).toBe(2);
  });

  it('ignores result messages for a different event', () => {
    const { store, messages, matches } = setup();
    messages.next({ type: 'match.result_declared', data: { ...matches[0], event_id: 'other-event', status: 'completed', result: { outcome: 'meron', revision: 1 } } });
    expect(store.current()?.match_number).toBe(135);
    expect(store.stats().total).toBe(0);
  });

  it('does not duplicate a result or replay its announcement on a repeated frame', () => {
    const { store, messages, matches } = setup(true);
    const frame = { type: 'match.result_declared', data: { ...matches[0], status: 'completed', result: { outcome: 'meron', revision: 1 } } };
    messages.next(frame);
    const history = store.matches();
    const announcement = store.announcement();
    messages.next(frame);
    expect(store.matches()).toBe(history);
    expect(store.announcement()).toBe(announcement);
    expect(store.stats().total).toBe(1);
  });

  it('rejects malformed result frames before altering the fight card', () => {
    const { store, messages, matches } = setup();
    const before = store.matches();
    for (const data of [
      null, { ...matches[0], match_number: undefined },
      { ...matches[0], status: 'completed', result: { outcome: 'unknown', revision: 1 } },
      { ...matches[0], status: 'completed', result: { outcome: 'wala', revision: -1 } },
      { ...matches[0], result: { outcome: 'wala', revision: 1 } },
    ]) messages.next({ type: 'match.result_declared', data } as RealtimeMessage);
    expect(store.matches()).toBe(before);
  });

  it('ignores an old result replay and stale in-progress frames after a correction', () => {
    const { store, messages, matches } = setup();
    messages.next({ type: 'match.result_corrected', data: { ...matches[0], status: 'completed', result: { outcome: 'wala', revision: 2 } } });
    messages.next({ type: 'match.result_declared', data: { ...matches[0], status: 'completed', result: { outcome: 'meron', revision: 1 } } });
    messages.next({ type: 'match.updated', data: { ...matches[0], status: 'in_progress', result: null } });
    expect(store.matches()[0].result?.outcome).toBe('wala');
    expect(store.matches()[0].status).toBe('completed');
  });

  it('adds a previously unseen official result immediately and keeps it through a delayed API snapshot', () => {
    vi.useFakeTimers();
    try {
      const { store, messages } = setup();
      messages.next({ type: 'match.result_declared', data: { ...match(134, 'completed'), result: { outcome: 'draw', revision: 1 } } });
      expect(store.matches().map(m => m.match_number)).toEqual([134, 135, 136]);
      expect(store.stats().draw).toBe(1);
      vi.advanceTimersByTime(450);
      expect(store.matches().map(m => m.match_number)).toEqual([134, 135, 136]);
      expect(store.stats().draw).toBe(1);
    } finally { vi.useRealTimers(); }
  });

});

import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject, filter, map } from 'rxjs';

import type { Prediction } from '@shared/models';
import { AuthStore, RealtimeService } from '@shared/services';
import { PlayerPicks, pickOutcome } from './player-picks';

const prediction = (over: Partial<Prediction> = {}): Prediction => ({
  id: 'p1', match_id: 'match-1', event_id: 'event-1', event_name: 'Derby', match_number: 1, meron_name: 'Red', wala_name: 'Blue',
  match_status: 'scheduled', official_outcome: null, side: 'meron', status: 'pending', open: true, changes: 0,
  created_at: '2026-10-29T00:00:00Z', updated_at: '2026-10-29T00:00:00Z', decided_at: null, ...over,
});
const page = (items: Prediction[]) => ({ items, total: items.length, page: 1, page_size: 100 });

describe('PlayerPicks', () => {
  let http: HttpTestingController;
  const user = signal<{ id: string } | null>({ id: 'player-a' });
  const messages = new Subject<{ type: string; data: unknown }>();

  beforeEach(() => {
    user.set({ id: 'player-a' });
    localStorage.setItem('esb.player.picks', '{"match-1":{"side":"meron","amount":500}}');
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(), provideHttpClientTesting(),
        { provide: AuthStore, useValue: { user } },
        { provide: RealtimeService, useValue: { on: (...types: string[]) => messages.pipe(filter((m) => types.includes(m.type)), map((m) => m.data)) } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());

  it('saves a pick through the API and keeps the server copy, not a local one', async () => {
    const picks = TestBed.inject(PlayerPicks);
    // The old device-only store is retired.
    expect(localStorage.getItem('esb.player.picks')).toBeNull();
    const saving = picks.choose('match-1', 'wala');
    const req = http.expectOne('/api/v1/portal/matches/match-1/prediction');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({ side: 'wala' }); // no amount: nothing is staked
    req.flush(prediction({ side: 'wala', changes: 1 }));
    await saving;
    expect(picks.pickFor('match-1')).toMatchObject({ side: 'wala', changes: 1 });
    expect(picks.saving()).toBeNull();
  });

  it('shows the refusal and reloads the canonical pick when the window has closed', async () => {
    const picks = TestBed.inject(PlayerPicks);
    const saving = picks.choose('match-1', 'draw');
    http.expectOne('/api/v1/portal/matches/match-1/prediction')
      .flush({ error: { code: 'prediction_closed', message: 'Predictions for this fight are closed', details: {}, request_id: null } }, { status: 409, statusText: 'Conflict' });
    await saving;
    expect(picks.error()).toBe('Predictions for this fight are closed');
    http.expectOne((r) => r.url === '/api/v1/portal/predictions' && r.params.get('match_id') === 'match-1').flush(page([prediction({ open: false })]));
    expect(picks.pickFor('match-1')).toMatchObject({ side: 'meron', open: false });
  });

  it('ignores a slower, older response for the same fight', () => {
    const picks = TestBed.inject(PlayerPicks);
    picks.loadMatch('match-1');
    picks.loadMatch('match-1');
    const [older, newer] = http.match((r) => r.params.get('match_id') === 'match-1');
    newer.flush(page([prediction({ side: 'draw' })]));
    older.flush(page([prediction({ side: 'meron' })]));
    expect(picks.pickFor('match-1')?.side).toBe('draw');
  });

  it('refreshes from the API when another device changes the pick or a result lands', () => {
    const picks = TestBed.inject(PlayerPicks);
    picks.loadEvent('event-1');
    http.expectOne((r) => r.params.get('event_id') === 'event-1').flush(page([prediction()]));
    messages.next({ type: 'prediction.updated', data: { match_id: 'match-1' } });
    http.expectOne((r) => r.params.get('match_id') === 'match-1').flush(page([prediction({ side: 'wala' })]));
    messages.next({ type: 'match.result_declared', data: { id: 'match-1' } });
    http.expectOne((r) => r.params.get('match_id') === 'match-1').flush(page([prediction({ side: 'wala', status: 'win', open: false })]));
    expect(pickOutcome(picks.pickFor('match-1'))).toBe('win');
    // Results for fights this player never picked cost no request.
    messages.next({ type: 'match.result_declared', data: { id: 'other-fight' } });
    http.expectNone((r) => r.params.get('match_id') === 'other-fight');
  });

  it('forgets the previous player when someone else signs in', () => {
    const picks = TestBed.inject(PlayerPicks);
    TestBed.tick();
    picks.loadEvent('event-1');
    http.expectOne((r) => r.params.get('event_id') === 'event-1').flush(page([prediction()]));
    user.set({ id: 'player-b' });
    TestBed.tick();
    expect(picks.pickFor('match-1')).toBeNull();
  });

  it('has an outcome only once the result is official', () => {
    expect(pickOutcome(prediction())).toBeNull();
    expect(pickOutcome(prediction({ status: 'lose' }))).toBe('lose');
    expect(pickOutcome(prediction({ status: 'void' }))).toBe('void');
    expect(pickOutcome(null)).toBeNull();
  });
});

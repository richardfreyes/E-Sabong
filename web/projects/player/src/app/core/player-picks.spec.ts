import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { PlayerPicks } from './player-picks';

describe('PlayerPicks', () => {
  beforeEach(() => {
    localStorage.removeItem('esb.player.picks');
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
  });

  it('keeps a pick on the device without calling the API', () => {
    const picks = TestBed.inject(PlayerPicks);
    picks.choose('match-1', 'meron', 500);

    expect(picks.pickFor('match-1')).toMatchObject({ side: 'meron', amount: 500 });
    expect(JSON.parse(localStorage.getItem('esb.player.picks')!)['match-1'].side).toBe('meron');
    // Nothing is staked, so nothing is sent anywhere.
    TestBed.inject(HttpTestingController).verify();
  });

  it('replaces a pick on the same fight and clears on request', () => {
    const picks = TestBed.inject(PlayerPicks);
    picks.choose('match-1', 'meron', 50);
    picks.choose('match-1', 'wala', 100);
    expect(picks.count()).toBe(1);
    expect(picks.pickFor('match-1')?.side).toBe('wala');

    picks.clear('match-1');
    expect(picks.pickFor('match-1')).toBeNull();
    expect(picks.count()).toBe(0);
  });

  it('reports no pick for a fight that was never marked', () => {
    const picks = TestBed.inject(PlayerPicks);
    expect(picks.pickFor(null)).toBeNull();
    expect(picks.pickFor('unknown')).toBeNull();
  });
});

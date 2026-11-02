import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { Subject, filter, map, of, throwError } from 'rxjs';

import type { BetPosition, BettingConfig, FightBook, PlayerBook } from '@shared/models';
import { AuthStore, PortalApi, RealtimeService } from '@shared/services';
import { FightBookStore, PlayWalletStore, estimatePays, reprice } from './play-betting';

const side = (stake: string, pays: string | null, result: null | 'won' = null) => ({ stake, pays, result });

function book(version: number, meron: string, wala: string, odds: [string | null, string | null], extra: Partial<FightBook> = {}): FightBook {
  return {
    match_id: 'm1', state: 'open', accepting: true, pools: { meron, wala, draw: '0.00' },
    odds: { meron: odds[0], wala: odds[1], meron_display: odds[0] && Number(odds[0]).toFixed(2), wala_display: odds[1] && Number(odds[1]).toFixed(2) },
    final: false, draw_multiplier: '8.00', commission_rate: '0.1000', bet_count: 2, outcome: null, version, ...extra,
  };
}

function view(b: FightBook, mine: BetPosition, balance = '9900.00', walletVersion = 2): PlayerBook {
  return {
    book: b, mine, wallet: { balance, version: walletVersion, disclaimer: '' },
    limits: { min_bet: '10.00', max_bet: '10000.00', max_fight_stake: '50000.00', remaining_fight_stake: '49900.00' },
  };
}

const MINE: BetPosition = { meron: side('100.00', '164.70'), wala: side('0.00', '0.00'), draw: side('0.00', '0.00'), settled_payout: null, settled_net: null };

function setup() {
  const messages = new Subject<{ type: string; data: unknown }>();
  const api = {
    book: vi.fn(() => of(view(book(3, '200.00', '166.00', ['1.647', '1.9843373493']), MINE))),
    betting: vi.fn(() => of<Partial<BettingConfig>>({ enabled: true, wallet: { balance: '10000.00', version: 1, disclaimer: '' } })),
    placeBet: vi.fn(),
  };
  TestBed.configureTestingModule({
    providers: [
      FightBookStore,
      { provide: PortalApi, useValue: api },
      { provide: AuthStore, useValue: { user: signal({ id: 'u1' }) } },
      { provide: RealtimeService, useValue: { on: (...types: string[]) => messages.pipe(filter((m) => types.includes(m.type)), map((m) => m.data)) } },
    ],
  });
  const store = TestBed.inject(FightBookStore);
  const wallet = TestBed.inject(PlayWalletStore);
  return { store, wallet, api, messages };
}

describe('estimatePays', () => {
  it('matches the server: stake x unrounded odds, half up to the cent', () => {
    expect(estimatePays('100.00', '1.647')).toBe('164.70');
    expect(estimatePays('100.00', '1.9455338188369828')).toBe('194.55'); // not 100 x 1.95
    expect(estimatePays('166.00', '1.9843373493975903')).toBe('329.40');
    expect(estimatePays('0.00', '1.5')).toBe('0.00');
    expect(estimatePays('50.00', null)).toBeNull();
  });

  it('reprices only open MERON/WALA positions', () => {
    const settled: BetPosition = { ...MINE, wala: side('50.00', '0.00', 'won'), draw: side('50.00', '400.00') };
    const next = reprice(settled, book(4, '200.00', '200.00', ['1.8', '1.8']));
    expect(next.meron.pays).toBe('180.00');
    expect(next.wala).toBe(settled.wala);
    expect(next.draw.pays).toBe('400.00');
  });
});

describe('FightBookStore', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('cancels an in-flight book request and clears initial loading when no fight is selected', () => {
    const { store, api } = setup();
    const pending = new Subject<PlayerBook>();
    api.book.mockReturnValueOnce(pending);
    store.watch('m1');
    expect(store.loading()).toBe(true);
    store.watch(null);
    expect(pending.observed).toBe(false);
    expect(store.loading()).toBe(false);
    pending.next(view(book(3, '1.00', '1.00', ['1.8', '1.8']), MINE));
    expect(store.book()).toBeNull();
  });

  it('keeps all-player pools apart from the player position and applies only newer frames', () => {
    const { store, messages } = setup();
    store.watch('m1');
    expect(store.book()?.pools.meron).toBe('200.00');
    expect(store.mine()?.meron.stake).toBe('100.00');

    // Another player's bet: pools and odds move, the player's stake does not, PAYS is re-estimated.
    messages.next({ type: 'book.updated', data: { ...book(4, '200.00', '200.00', ['1.8', '1.8']), event_id: 'e1' } });
    expect(store.book()?.pools.wala).toBe('200.00');
    expect(store.mine()?.meron.stake).toBe('100.00');
    expect(store.mine()?.meron.pays).toBe('180.00');

    // Late, repeated and foreign frames are ignored.
    messages.next({ type: 'book.updated', data: book(3, '1.00', '1.00', ['0.9', '0.9']) });
    messages.next({ type: 'book.updated', data: book(4, '9.00', '9.00', ['0.9', '0.9']) });
    messages.next({ type: 'book.updated', data: { ...book(9, '5.00', '5.00', ['0.9', '0.9']), match_id: 'other' } });
    expect(store.book()?.version).toBe(4);
    expect(store.book()?.pools.meron).toBe('200.00');
  });

  it('drops the "bet accepted" notice once betting closes', async () => {
    const { store, api, messages } = setup();
    store.watch('m1');
    api.placeBet.mockReturnValue(of({ ...view(book(4, '300.00', '166.00', ['1.4', '2.5']), MINE), bet: { amount: '100.00' }, replayed: false }));
    await store.place('meron', '100.00');
    expect(store.notice()).toContain('Bet accepted');
    messages.next({ type: 'book.updated', data: book(5, '300.00', '166.00', ['1.4', '2.5'], { state: 'closed', accepting: false, final: true }) });
    expect(store.notice()).toBeNull();
  });

  it('resynchronises after a reconnect and when this player bets from another device', () => {
    const { store, api, messages } = setup();
    store.watch('m1');
    messages.next({ type: 'connected', data: {} });
    messages.next({ type: 'bets.updated', data: { match_id: 'm1' } });
    messages.next({ type: 'bets.updated', data: { match_id: 'elsewhere' } });
    expect(api.book).toHaveBeenCalledTimes(3);
  });

  it('keeps a newer frame when an older API response arrives afterwards', () => {
    const { store, api, messages } = setup();
    const pending = new Subject<PlayerBook>();
    api.book.mockReturnValueOnce(of(view(book(3, '200.00', '166.00', ['1.647', '1.98']), MINE))).mockReturnValueOnce(pending);
    store.watch('m1');
    store.reload();
    messages.next({ type: 'book.updated', data: book(6, '300.00', '300.00', ['1.8', '1.8']) });
    pending.next(view(book(5, '250.00', '250.00', ['1.8', '1.8']), MINE));
    expect(store.book()?.version).toBe(6);
    expect(store.mine()?.meron.pays).toBe('180.00');
  });

  it('places a bet once, applies the server figures and updates the wallet', async () => {
    const { store, wallet, api } = setup();
    store.watch('m1');
    const after = view(book(5, '300.00', '166.00', ['1.098', '1.9843']), { ...MINE, meron: side('200.00', '219.60') }, '9800.00', 3);
    api.placeBet.mockReturnValue(of({ ...after, bet: { amount: '100.00' }, replayed: false }));
    const first = store.place('meron', '100.00');
    expect(await store.place('wala', '100.00')).toBe(false); // a second click while submitting is ignored
    expect(await first).toBe(true);
    expect(api.placeBet).toHaveBeenCalledTimes(1);
    expect(store.mine()?.meron.stake).toBe('200.00');
    expect(wallet.balance()).toBe('9800.00');
    expect(store.notice()).toContain('MERON');
  });

  it('resends a bet with the same request id after a lost response, and reports server errors', async () => {
    const { store, api } = setup();
    store.watch('m1');
    const ok = view(book(5, '300.00', '166.00', ['1.098', '1.98']), MINE);
    api.placeBet
      .mockReturnValueOnce(throwError(() => new HttpErrorResponse({ status: 0 })))
      .mockReturnValueOnce(of({ ...ok, bet: { amount: '100.00' }, replayed: true }));
    expect(await store.place('meron', '100.00')).toBe(true);
    const [first, second] = api.placeBet.mock.calls as unknown as [string, string, string, string][];
    expect(first[3]).toBe(second[3]);

    api.placeBet.mockReturnValueOnce(
      throwError(() => new HttpErrorResponse({ status: 409, error: { error: { code: 'betting_closed', message: 'Betting is closed for this fight' } } })),
    );
    expect(await store.place('wala', '50.00')).toBe(false);
    expect(store.error()).toBe('Betting is closed for this fight');
  });
});

describe('PlayWalletStore', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('loads from the API and never goes back to an older balance', () => {
    const { wallet, messages } = setup();
    wallet.load();
    expect(wallet.enabled()).toBe(true);
    expect(wallet.balance()).toBe('10000.00');
    messages.next({ type: 'wallet.updated', data: { balance: '9900.00', version: 3 } });
    messages.next({ type: 'wallet.updated', data: { balance: '10000.00', version: 2 } });
    expect(wallet.balance()).toBe('9900.00');
    expect(wallet.pulse()).toBe(2);
  });

  it('turns the betting board on when an administrator enables betting, without a reload', () => {
    const { wallet, api, messages } = setup();
    api.betting.mockReturnValueOnce(of({ enabled: false, wallet: null }));
    wallet.load();
    expect(wallet.enabled()).toBe(false);
    messages.next({ type: 'betting.config_updated', data: { enabled: true } });
    expect(api.betting).toHaveBeenCalledTimes(2);
    expect(wallet.enabled()).toBe(true);
    expect(wallet.balance()).toBe('10000.00');
  });
});

import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, filter, map, of } from 'rxjs';

import type { FightBookSummary, PlayReconciliation } from '@shared/models';
import { AdminApi, AuthStore, RealtimeService } from '@shared/services';
import { BettingPage } from './betting';

const OPEN = {
  match_id: 'm1', event_id: 'e1', event_name: 'Grand Derby', match_number: 3, match_status: 'in_progress', state: 'open', accepting: true,
  pools: { meron: '200.00', wala: '166.00', draw: '50.00' },
  odds: { meron: '1.647', wala: '1.98', meron_display: '1.65', wala_display: '1.98' },
  final: false, draw_multiplier: '8.00', commission_rate: '0.1000', bet_count: 3, outcome: null, version: 4, stakes_total: '416.00',
  obligations: { meron: '329.40', wala: '329.40', draw: '766.00', cancelled: '416.00' }, settlement: null, opened_at: null, closed_at: null,
} as FightBookSummary;
const SETTLED = {
  ...OPEN, match_id: 'm2', match_number: 2, state: 'settled', accepting: false, final: true, outcome: 'meron', obligations: null,
  settlement: { outcome: 'meron', revision: 1, stakes: '416.00', paid_to_winners: '329.40', refunds: '0.00', commission: '36.60', uncollected: '0.00', settled_at: null },
} as FightBookSummary;
const RECON: PlayReconciliation = {
  granted: '3000.00', grants: 3, staked: '516.00', paid_to_winners: '329.40', refunded: '0.00', resettled: '0.00', in_wallets: '2813.40', wallets: 3,
  at_stake: '100.00', house_result: '86.60', commission: '36.60', balanced: true, disclaimer: '',
  checks: { wallets_match_ledger: true, wallet_mismatches: 0, pool_mismatches: 0, uncollected_bets: 0, uncollected_amount: '0.00' },
};

function setup(permissions: string[]) {
  const messages = new Subject<{ type: string; data: unknown }>();
  const page = <T,>(items: T[]) => of({ items, total: items.length, page: 1, page_size: 25 });
  const api = {
    bettingBooks: vi.fn(() => page([OPEN, SETTLED])),
    playWallets: vi.fn(() => page([])),
    playEntries: vi.fn(() => page([])),
    playReconciliation: vi.fn(() => of(RECON)),
  };
  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      { provide: AdminApi, useValue: api },
      { provide: AuthStore, useValue: { user: signal({ id: 'a' }), hasPermission: (p: string) => permissions.includes(p) } },
      { provide: RealtimeService, useValue: { on: (...types: string[]) => messages.pipe(filter((m) => types.includes(m.type)), map((m) => m.data)) } },
    ],
  });
  const fixture = TestBed.createComponent(BettingPage);
  fixture.detectChanges();
  return { fixture, api, messages, text: () => (fixture.nativeElement as HTMLElement).textContent!.replace(/\s+/g, ' ') };
}

describe('BettingPage', () => {
  afterEach(() => vi.useRealTimers());

  it('shows live pools, odds and returns by result, and settlement results, from the API', () => {
    const { text } = setup(['points.view_all']);
    const t = text();
    expect(t).toContain('Fight #3');
    expect(t).toContain('200.00');
    expect(t).toContain('Odds 1.65');
    expect(t).toContain('If MERON wins, returns329.40');
    expect(t).toContain('If DRAW, returns766.00');
    expect(t).toContain('Settled · MERON');
    expect(t).toContain('Credited to winners329.40');
    expect(t).toContain('Deduction36.60');
    expect(t).toContain('Granted by the system');
    expect(t).toContain('3,000.00');
    // Play credits are never presented as money.
    expect(t).not.toMatch(/₱|PHP|peso|payout/i);
    expect(t).toContain('Balances have no cash value');
    expect(t).toContain('Balanced');
  });

  it('keeps wallet and ledger data away from staff without points.view_all', () => {
    const { api, text } = setup(['events.view']);
    expect(api.playWallets).not.toHaveBeenCalled();
    expect(api.playReconciliation).not.toHaveBeenCalled();
    expect(text()).not.toContain('Granted by the system');
    expect(text()).toContain('Fight #3');
  });

  it('refetches once per burst of realtime frames', () => {
    vi.useFakeTimers();
    const { api, messages } = setup(['points.view_all']);
    for (const type of ['book.updated', 'book.updated', 'fight.settled', 'betting.ledger_updated']) messages.next({ type, data: {} });
    vi.advanceTimersByTime(450);
    expect(api.bettingBooks).toHaveBeenCalledTimes(2);
    expect(api.playReconciliation).toHaveBeenCalledTimes(2);
  });
});

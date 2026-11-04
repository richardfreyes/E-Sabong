import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';
import { PortalApi, RealtimeService } from '@shared/services';
import { PlayWalletStore } from '../core/play-betting';
import { ArenaPointsPage } from './arena-points';

async function setup() {
  const balance = signal<string | null>('1234.56');
  const messages = new Subject<unknown>();
  const wallet = { balance, load: vi.fn() };
  const statement = { user_id: 'u', username: 'ana', balance: '1234.56', granted: '1000.00', staked: '125.00', winnings_credited: '180.00',
    refunds: '20.00', corrections: '0.00', net_result: '75.00', bets: 3, bets_won: 1, bets_lost: 1, bets_refunded: 1, bets_open: 0,
    lost_stakes: '25.00', open_stakes: '0.00', entries: 6 };
  const api = {
    arenaPointsHistory: vi.fn(() => of({ items: [{ id: 'entry', kind: 'stake', delta: '-25.00', balance_after: '1234.56', created_at: '2026-11-02T01:00:00Z',
      bet_side: 'meron', bet_status: 'lost', match_number: 3, event_name: 'MAIN' }], total: 1, page: 1, page_size: 25 })),
    myStatement: vi.fn((_q?: Record<string, unknown>) => of(statement)),
  };
  TestBed.configureTestingModule({ providers: [
    { provide: PortalApi, useValue: api }, { provide: PlayWalletStore, useValue: wallet },
    { provide: RealtimeService, useValue: { on: () => messages } },
  ] });
  const fixture = TestBed.createComponent(ArenaPointsPage);
  fixture.detectChanges();
  await fixture.whenStable();
  return { fixture, wallet, api, messages };
}

describe('Balance statement', () => {
  it('uses the shared gameplay balance and actual ledger amounts', async () => {
    const { fixture, wallet } = await setup();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-field="balance"]')?.textContent).toBe('1,234.56');
    expect(el.textContent).toContain('−25.00');
    expect(el.textContent).toContain('Balance 1,234.56');
    expect(el.textContent).not.toMatch(/\b(?:cr|pts|credits|PHP)\b|₱/i);
    wallet.balance.set(null);
    fixture.detectChanges();
    expect(el.querySelector('[data-field="balance"]')?.textContent).toBe('—');
  });

  it('refreshes entries on balance changes and reconnects without issuing writes', async () => {
    const { fixture, wallet, api, messages } = await setup();
    const before = api.arenaPointsHistory.mock.calls.length;
    wallet.balance.set('1000.00');
    fixture.detectChanges();
    expect(api.arenaPointsHistory.mock.calls.length).toBe(before + 1);
    messages.next({});
    expect(api.arenaPointsHistory.mock.calls.length).toBe(before + 2);
    expect(wallet.load).toHaveBeenCalledTimes(1);
  });

  it('shows statement totals and each bet\'s outcome, including losses', async () => {
    const { fixture } = await setup();
    const el = fixture.nativeElement as HTMLElement;
    const totals = el.querySelector('[data-testid="statement-totals"]')!.textContent!;
    expect(totals).toContain('Lost stakes');
    expect(totals).toContain('1 lost');
    expect(el.querySelector('[data-field="net"]')?.textContent).toBe('+75.00');
    expect(el.textContent).toContain('Bet placed · MERON · Fight #3, MAIN');
    expect(el.querySelector('[data-field="outcome"]')?.textContent).toBe('Lost');
  });

  it('filters by type and date; dates also re-scope the totals', async () => {
    const { fixture, api } = await setup();
    const page = fixture.componentInstance;
    page.setFilter('kind', 'payout');
    expect(api.arenaPointsHistory).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'payout' }));
    const statements = api.myStatement.mock.calls.length;
    page.setFilter('date_from', '2026-11-01');
    expect(api.arenaPointsHistory).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'payout', date_from: '2026-11-01' }));
    expect(api.myStatement.mock.calls.length).toBe(statements + 1);
    expect(api.myStatement).toHaveBeenLastCalledWith({ date_from: '2026-11-01', date_to: null });
  });
});

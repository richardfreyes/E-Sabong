import { NO_ERRORS_SCHEMA } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { AdminApi, ApiError } from '@shared/services';
import { BalanceHistoryPage } from './balance-history';
import { UserPickerComponent } from './user-picker';

const ENTRY = {
  id: 'e1', created_at: '2026-11-04T03:00:00Z', user_id: 'u1', username: 'ana', kind: 'stake', delta: '-100.00', balance_after: '900.00',
  reference: 'stake:abc', memo: null, match_id: 'm1', match_number: 3, event_name: 'MAIN',
  bet_id: 'b1', bet_side: 'meron', bet_status: 'lost', bet_amount: '100.00', bet_credited: '0.00',
};
const STATEMENT = {
  user_id: 'u1', username: 'ana', balance: '900.00', granted: '1000.00', staked: '100.00', winnings_credited: '0.00', refunds: '0.00',
  corrections: '0.00', net_result: '-100.00', bets: 1, bets_won: 0, bets_lost: 1, bets_refunded: 0, bets_open: 0,
  lost_stakes: '100.00', open_stakes: '0.00', entries: 2,
};

function setup(api: Partial<Record<keyof AdminApi, unknown>> = {}) {
  const mock = {
    playEntries: vi.fn(() => of({ items: [ENTRY], total: 1, page: 1, page_size: 25 })),
    playStatement: vi.fn(() => of(STATEMENT)),
    ...api,
  };
  TestBed.configureTestingModule({ providers: [{ provide: AdminApi, useValue: mock }] });
  // The account picker talks to the users API; it is outside this page's behaviour.
  TestBed.overrideComponent(BalanceHistoryPage, { remove: { imports: [UserPickerComponent] }, add: { schemas: [NO_ERRORS_SCHEMA] } });
  const fixture = TestBed.createComponent(BalanceHistoryPage);
  fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  return { fixture, el, page: fixture.componentInstance, api: mock, text: () => el.textContent!.replace(/\s+/g, ' ') };
}

describe('Balance History', () => {
  it('lists every player\'s entries with outcome, reference and UTC time, and keeps the no-cash-value notice', () => {
    const { api, el, text } = setup();
    expect(api.playEntries).toHaveBeenCalledTimes(1);
    expect(el.querySelector('[data-testid="no-cash-value"]')!.textContent).toContain('no cash value');
    const t = text();
    expect(t).toContain('MERON · Lost');
    expect(t).toContain('stake:abc');
    expect(t).toContain('Nov 4, 2026, 03:00 UTC');
    expect(t).toContain('MAIN · #3');
    expect(t).not.toMatch(/₱|PHP|peso|cash out|withdraw now/i);
    expect(api.playStatement).not.toHaveBeenCalled(); // totals only for one selected player
  });

  it('scopes to one player with statement totals, and filters by type and UTC dates', () => {
    const { fixture, page, api, el } = setup();
    page.selectPlayer({ id: 'u1', username: 'ana' } as never);
    fixture.detectChanges();
    expect(api.playEntries).toHaveBeenLastCalledWith(expect.objectContaining({ user_id: 'u1', page: 1 }));
    expect(api.playStatement).toHaveBeenLastCalledWith('u1', { date_from: null, date_to: null });
    const totals = el.querySelector('[data-testid="statement-totals"]')!.textContent!;
    expect(totals).toContain('Losses');
    expect(totals).toContain('1 lost');
    expect(el.querySelector('[data-field="net"]')!.textContent).toBe('−100.00');

    page.setKind('payout');
    expect(api.playEntries).toHaveBeenLastCalledWith(expect.objectContaining({ user_id: 'u1', kind: 'payout' }));
    page.setDate('date_from', '2026-11-01');
    page.setDate('date_to', '2026-11-04');
    expect(api.playEntries).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'payout', date_from: '2026-11-01', date_to: '2026-11-04' }));
    expect(api.playStatement).toHaveBeenLastCalledWith('u1', { date_from: '2026-11-01', date_to: '2026-11-04' });

    page.selectPlayer(null);
    fixture.detectChanges();
    expect(api.playEntries).toHaveBeenLastCalledWith(expect.objectContaining({ user_id: null }));
    expect(el.querySelector('[data-testid="statement-totals"]')).toBeNull();
  });

  it('shows the API refusal for the statement and can retry', () => {
    const playStatement = vi.fn(() => throwError(() => new ApiError(403, 'forbidden', 'Permission denied')));
    const { fixture, page, el } = setup({ playStatement });
    page.selectPlayer({ id: 'u1', username: 'ana' } as never);
    fixture.detectChanges();
    expect(el.querySelector('[role="alert"]')!.textContent).toContain('Permission denied');
    playStatement.mockReturnValue(of(STATEMENT) as never);
    page.loadStatement();
    fixture.detectChanges();
    expect(el.querySelector('[data-testid="statement-totals"]')).not.toBeNull();
  });

  it('passes loading, empty and error states to the table', () => {
    const { page } = setup({ playEntries: vi.fn(() => throwError(() => new ApiError(500, 'server', 'Ledger unavailable'))) });
    expect(page.entries.error()).toBe('Ledger unavailable');
    expect(page.entries.loading()).toBe(false);
  });

  it('has an empty state when there is no activity', () => {
    const { page } = setup({ playEntries: vi.fn(() => of({ items: [], total: 0, page: 1, page_size: 25 })) });
    expect(page.entries.rows()).toEqual([]);
    expect(page.entries.total()).toBe(0);
  });

  it('only reads: it exposes no write call', () => {
    const { page } = setup();
    const methods = Object.getOwnPropertyNames(Object.getPrototypeOf(page));
    expect(methods.filter((m) => /transfer|cash|redeem|withdraw|approve|reserve|payout|settle/i.test(m))).toEqual([]);
  });
});

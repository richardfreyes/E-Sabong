import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Subject, filter, map, of, throwError } from 'rxjs';

import { ConfirmService } from '@shared/components';
import type { BalanceAccount, BalanceOperation, BalanceSummary, PlayWalletEntry } from '@shared/models';
import { AdminApi, ApiError, AuthStore, RealtimeService, ToastService } from '@shared/services';
import { BalanceManagementPage } from './balance-management';

const SUMMARY: BalanceSummary = {
  accounts: 4, participants: 3, total_balance: '3150.00', pending_corrections: 1, reset_policy: 'starting_balance', starting_balance: '1000.00',
  can_manage: true, disclaimer: '',
};
const ANA: BalanceAccount = {
  id: 'u1', username: 'ana', full_name: 'Ana', account_type: 'player', status: 'active', parent_id: 'ag', parent_username: 'agt', balance: '1050.00',
  has_wallet: true, updated_at: null,
};
const GRANT_ROW: PlayWalletEntry = {
  id: 'e1', created_at: '2026-11-04T03:00:00Z', user_id: 'u1', username: 'ana', kind: 'uniform_grant', delta: '50.00', balance_after: '1050.00',
  reference: 'ugrant:x:u1', memo: 'Kick-off', match_id: null, match_number: null, event_name: null, balance_before: '1000.00', operation_id: 'op1',
  operation_reference: 'BAL-20261104-AB12CD34', operation_kind: 'uniform_grant', operation_status: 'applied', operation_remarks: 'Kick-off',
};
const PENDING: BalanceOperation = {
  id: 'op2', reference: 'BAL-20261104-PENDING1', kind: 'correction', status: 'pending', amount: '50.00', direction: 'debit', target_user_id: 'u1',
  target_username: 'ana', corrects_entry_id: 'e1', corrects_operation_id: null, remarks: 'Duplicate grant', created_by_id: 'other-admin',
  created_by_username: 'adm2', reviewed_by_id: null, reviewed_by_username: null, reviewed_at: null, review_remarks: null, accounts_affected: 0,
  total_delta: '0.00', created_at: '2026-11-04T04:00:00Z', replayed: false, disclaimer: '',
};
const MINE: BalanceOperation = { ...PENDING, id: 'op3', reference: 'BAL-20261104-MINE0001', created_by_id: 'me' };
const APPLIED: BalanceOperation = { ...PENDING, id: 'op1', reference: 'BAL-20261104-AB12CD34', kind: 'uniform_grant', status: 'applied', direction: null, target_user_id: null, target_username: null, accounts_affected: 3, total_delta: '150.00', remarks: 'Kick-off' };

function setup(opts: { summary?: BalanceSummary; type?: string; permissions?: string[]; confirm?: string | null } = {}) {
  const messages = new Subject<{ type: string; data: unknown }>();
  const page = <T,>(items: T[]) => of({ items, total: items.length, page: 1, page_size: 25 });
  const api = {
    balanceSummary: vi.fn(() => of(opts.summary ?? SUMMARY)),
    balanceAccounts: vi.fn(() => page([ANA])),
    balanceAccount: vi.fn(() => of({ account: ANA, statement: { granted: '1000.00', uniform_grants: '50.00', resets: '0.00', balance_corrections: '0.00', net_result: '0.00' }, disclaimer: '' })),
    balanceEntries: vi.fn(() => page([GRANT_ROW])),
    balanceOperations: vi.fn(() => page([PENDING, MINE, APPLIED])),
    accountGrant: vi.fn(() => of({ ...APPLIED, id: 'op8', reference: 'BAL-ONE', kind: 'account_grant', amount: '40.00', target_user_id: 'u1', target_username: 'ana', accounts_affected: 1, total_delta: '40.00' })),
    uniformGrant: vi.fn(() => of({ ...APPLIED, id: 'op9', reference: 'BAL-NEW', amount: '25.00' })),
    resetBalances: vi.fn(() => of({ ...APPLIED, kind: 'reset' })),
    proposeCorrection: vi.fn(() => of(PENDING)),
    approveCorrection: vi.fn(() => of({ ...PENDING, status: 'applied' })),
    rejectCorrection: vi.fn(() => of({ ...PENDING, status: 'rejected' })),
  };
  const confirm = { ask: vi.fn(async () => (opts.confirm === undefined ? '' : opts.confirm)) };
  const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
  const permissions = opts.permissions ?? ['points.view_all', 'balance.manage'];
  TestBed.configureTestingModule({
    imports: [NoopAnimationsModule],
    providers: [
      { provide: AdminApi, useValue: api },
      { provide: ConfirmService, useValue: confirm },
      { provide: ToastService, useValue: toast },
      { provide: AuthStore, useValue: { user: signal({ id: 'me', account_type: opts.type ?? 'admin' }), hasPermission: (p: string) => permissions.includes(p) } },
      { provide: RealtimeService, useValue: { on: (...types: string[]) => messages.pipe(filter((m) => types.includes(m.type)), map((m) => m.data)) } },
    ],
  });
  const fixture = TestBed.createComponent(BalanceManagementPage);
  fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  return { fixture, el, page: fixture.componentInstance, api, confirm, toast, messages, text: () => el.textContent!.replace(/\s+/g, ' ') };
}

const flush = () => new Promise((r) => setTimeout(r));

describe('Balance Management', () => {
  it('shows administrators the summary, the selected-player action and the no-cash-value notice without currency wording', () => {
    const { el, text, api } = setup();
    expect(api.balanceSummary).toHaveBeenCalled();
    expect(el.querySelector('[data-testid="no-cash-value"]')!.textContent).toContain('no cash value');
    expect(el.querySelector('[data-testid="balance-actions"]')).not.toBeNull();
    const t = text();
    expect(t).toContain('Add to this account');
    expect(t).not.toContain('Grant to every player');
    expect(t).toContain('3,150.00');
    expect(t).toContain('Adjustments (1)');
    expect(t).not.toMatch(/₱|PHP|peso|transfer to|send balance|load balance|cash out|withdraw now/i);
  });

  it('keeps master agents and agents read-only: no actions and no tenant-wide adjustments', () => {
    const { el, text, api } = setup({ summary: { ...SUMMARY, can_manage: false, pending_corrections: null }, type: 'agent', permissions: ['points.view_downline'] });
    expect(el.querySelector('[data-testid="balance-actions"]')).toBeNull();
    expect(text()).not.toContain('Adjustments');
    expect(api.balanceOperations).not.toHaveBeenCalled();
    expect(text()).toContain('assigned to you');
  });

  it('adds balance only to the selected player and reuses its idempotency key on retry', async () => {
    const { page, api, confirm, toast, fixture, el } = setup();
    await page.accountGrant();
    expect(api.accountGrant).not.toHaveBeenCalled();
    page.select(ANA);
    fixture.detectChanges();
    expect(el.querySelector('[data-testid="account-grant-submit"]')!.textContent).toContain('ana');
    page.accountGrantForm.setValue({ amount: '40', remarks: 'Opening' });
    api.accountGrant.mockReturnValueOnce(throwError(() => new ApiError(0, 'network', 'Offline')));
    await page.accountGrant();
    expect(confirm.ask).toHaveBeenCalledWith(expect.objectContaining({ title: 'Add balance to ana?' }));
    await page.accountGrant();
    const [first, second] = api.accountGrant.mock.calls.map((c) => (c as unknown[])[0] as { idempotency_key: string; target_user_id: string; amount: string; remarks: string });
    expect(first.idempotency_key).toBe(second.idempotency_key);
    expect(second).toEqual({ target_user_id: 'u1', amount: '40', remarks: 'Opening', idempotency_key: first.idempotency_key });
    expect(Object.keys(second)).not.toContain('tenant_id');
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('added to ana'));
  });

  it('refuses invalid amounts before calling the API, and does nothing when the dialog is cancelled', async () => {
    const { page, api } = setup({ confirm: null });
    page.select(ANA);
    page.accountGrantForm.setValue({ amount: '-5', remarks: '' });
    await page.accountGrant();
    page.accountGrantForm.setValue({ amount: '1.234', remarks: '' });
    await page.accountGrant();
    page.accountGrantForm.setValue({ amount: '10', remarks: '' });
    await page.accountGrant();
    expect(api.accountGrant).not.toHaveBeenCalled();
  });

  it('explains a refused reset in plain language', async () => {
    const { page, api, toast } = setup();
    api.resetBalances.mockReturnValueOnce(throwError(() => new ApiError(409, 'open_stakes', 'Balances cannot be reset')));
    await page.reset();
    expect(api.resetBalances).toHaveBeenCalledWith({ remarks: null, idempotency_key: expect.stringMatching(/^reset-/) });
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('stakes are open'));
  });

  it('selects a player through the searchable account list, filters history and proposes a reviewed correction', async () => {
    const { page, api, fixture, el } = setup();
    page.select(ANA);
    fixture.detectChanges();
    expect(api.balanceEntries).toHaveBeenLastCalledWith(expect.objectContaining({ user_id: 'u1' }));
    expect(el.querySelector('[data-testid="selected-account"]')!.textContent).toContain('1,050.00');
    expect(page.correctable().map((e) => e.id)).toEqual(['e1']);
    page.correctionForm.setValue({ basis: 'entry:e1', amount: '20', remarks: 'Duplicate grant issued' });
    await page.propose();
    expect(api.proposeCorrection).toHaveBeenCalledWith(expect.objectContaining({ target_user_id: 'u1', entry_id: 'e1', amount: '20', remarks: 'Duplicate grant issued' }));
  });

  it('lets an administrator review only corrections proposed by someone else', async () => {
    const { page, api, fixture, el } = setup({ confirm: 'Checked' });
    page.tab = 2;
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const t = el.textContent!;
    expect(t).toContain('BAL-20261104-PENDING1');
    expect(t).toContain('Needs another admin');
    await page.approve(PENDING);
    expect(api.approveCorrection).toHaveBeenCalledWith('op2', 'Checked');
    await page.reject(PENDING);
    expect(api.rejectCorrection).toHaveBeenCalledWith('op2', 'Checked');
  });

  it('refreshes when the ledger changes', async () => {
    const { api, messages } = setup();
    const before = api.balanceSummary.mock.calls.length;
    messages.next({ type: 'betting.ledger_updated', data: {} });
    await new Promise((r) => setTimeout(r, 350));
    await flush();
    expect(api.balanceSummary.mock.calls.length).toBeGreaterThan(before);
  });
});

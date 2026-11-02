import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { EMPTY, Subject, of, throwError } from 'rxjs';
import { ConfirmService } from '@shared/components';
import type { PointTransaction, PointsSummary, TenantProfile, User } from '@shared/models';
import { AdminApi, AuthStore, RealtimeService, ToastService } from '@shared/services';
import { PointsPage } from './points';

const recipient = { id: 'recipient', username: 'player1', full_name: 'Player', account_type: 'player', balance: 50 } as User;
const sender = { id: 'sender', username: 'master1', full_name: 'Master', account_type: 'master_agent', balance: 40 } as User;
const party = (user_id: string | null, username: string | null) => ({ account_id: `acct-${user_id}`, kind: user_id ? 'user' : 'issuance', user_id, username, full_name: null });
const transaction = {
  id: 'tx-1', reference: 'tx-1', kind: 'transfer', type: 'transfer', direction: null, status: 'completed', amount: 25, memo: null,
  source: party('admin-id', 'admin'), destination: party('recipient', 'player1'), created_by_id: 'admin-id', created_by_username: 'admin',
  created_at: '2026-10-31T01:00:00Z', tenant_id: 't', source_balance_before: 100, source_balance_after: 75,
  destination_balance_before: 50, destination_balance_after: 75, reverses_transaction_id: null, refund_transaction_id: null,
} as PointTransaction;
const summary: PointsSummary = {
  scope: 'tenant', own_balance: 100, total_issued: 150, circulating: 150, accounts: 2, transactions_30d: {}, disclaimer: '',
  gross_issued: 150, total_transferred: 30, total_received: 5, total_distributed: 45, completed_transactions: 7,
};

async function setup(role = 'admin', permissions: string[] | null = null) {
  const api = {
    pointsSummary: vi.fn(() => of(summary)),
    accountEntries: vi.fn((_id: string, _q?: unknown) => of<{ items: unknown[]; total: number; page: number; page_size: number }>({ items: [], total: 0, page: 1, page_size: 6 })),
    transactions: vi.fn(() => of({ items: [], total: 0, page: 1, page_size: 25 })),
    users: vi.fn(() => of({ items: [], total: 0, page: 1, page_size: 25 })),
    user: vi.fn((id: string) => of(id === sender.id ? sender : recipient)),
    hierarchy: vi.fn(() => of([])),
    allocate: vi.fn((_body: unknown, _key?: string) => of({ ...transaction, kind: 'allocation', type: 'issue' })),
    transfer: vi.fn((_body: unknown, _key?: string) => of(transaction) as import('rxjs').Observable<PointTransaction>),
    adjust: vi.fn((_body: unknown, _key?: string) => of({ ...transaction, kind: 'adjustment', type: 'adjustment' })),
  };
  const confirm = { ask: vi.fn<(...args: any[]) => Promise<string | null>>().mockResolvedValue('') };
  const toast = { success: vi.fn() };
  const has = (p: string) => permissions === null || permissions.includes(p);
  TestBed.configureTestingModule({ providers: [
    { provide: AdminApi, useValue: api },
    { provide: AuthStore, useValue: { user: signal({ id: 'admin-id', username: 'admin', account_type: role } as TenantProfile), hasPermission: has } },
    { provide: RealtimeService, useValue: { on: () => EMPTY } },
    { provide: ConfirmService, useValue: confirm },
    { provide: ToastService, useValue: toast },
  ] });
  const fixture = TestBed.createComponent(PointsPage);
  await fixture.whenStable();
  const page = fixture.componentInstance;
  const text = () => { fixture.detectChanges(); return (fixture.nativeElement as HTMLElement).textContent ?? ''; };
  return { fixture, page, api, confirm, toast, text };
}

function fill(page: PointsPage, amount = 25) {
  page.setOp('transfer');
  page.form.patchValue({ user_id: recipient.id, amount, memo: 'Approved distribution' });
  page.recipient.set(recipient);
}

describe('Points wallet overview', () => {
  it('renders server balances and totals, never client-side figures', async () => {
    const { text, api } = await setup();
    const content = text();
    for (const label of ['Your balance', 'Total issued', 'Outgoing transfers', 'Incoming transfers', 'Distributed to users', 'Completed transactions']) {
      expect(content).toContain(label);
    }
    expect(content).toContain('100');
    expect(content).toContain('45');
    expect(api.pointsSummary).toHaveBeenCalledTimes(1);
    expect(api.accountEntries).toHaveBeenCalledWith('admin-id', { page_size: 6 });
    expect(content).not.toMatch(/demo/i);
  });

  it('shows recent wallet activity from the ledger', async () => {
    const { api, page, text } = await setup();
    api.accountEntries.mockReturnValue(of({ items: [
      { id: 'e1', transaction_id: 'tx-1', kind: 'transfer', entry_type: 'debit', delta: -25, balance_before: 100, balance_after: 75, memo: null,
        counterparty: party('recipient', 'player1'), created_at: '2026-10-31T01:00:00Z' },
    ], total: 1, page: 1, page_size: 6 }));
    page.loadRecent();
    const content = text();
    expect(content).toContain('player1');
    expect(content).toContain('−25');
  });
});

describe('Create points', () => {
  it('credits the signed-in Admin wallet by default after an explicit confirmation', async () => {
    const { page, api, confirm } = await setup();
    page.setOp('issue');
    page.form.patchValue({ amount: 25, memo: 'Treasury funding', user_id: 'ignored-account' });
    await page.submit();
    expect(api.allocate).toHaveBeenCalledWith({ user_id: 'admin-id', amount: 25, memo: 'Treasury funding' }, expect.any(String));
    expect(confirm.ask.mock.calls[0][0].message).toContain('Issued supply increases by 25');
    expect(api.transfer).not.toHaveBeenCalled();
  });

  it('credits a selected account directly and requires that selection', async () => {
    const { page, api, confirm } = await setup();
    page.setOp('issue');
    page.form.patchValue({ target: 'other', amount: 10, memo: 'Direct credit' });
    await Promise.resolve();
    await page.submit();
    expect(confirm.ask).not.toHaveBeenCalled();
    page.form.patchValue({ user_id: recipient.id, amount: 10, memo: 'Direct credit' });
    page.recipient.set(recipient);
    await page.submit();
    expect(api.allocate).toHaveBeenCalledWith({ user_id: 'recipient', amount: 10, memo: 'Direct credit' }, expect.any(String));
    expect(confirm.ask.mock.calls[0][0].message).toContain('player1');
  });

  it('requires a reason of at least three characters', async () => {
    const { page, api, confirm } = await setup();
    page.setOp('issue');
    for (const memo of ['', '  ', 'ab']) {
      page.form.patchValue({ amount: 25, memo });
      await page.submit();
    }
    expect(confirm.ask).not.toHaveBeenCalled();
    expect(api.allocate).not.toHaveBeenCalled();
  });
});

describe('Transfer points', () => {
  it.each([0, -5, 1.5, 1_000_000_001, null])('rejects amount %s before confirmation', async amount => {
    const { page, api, confirm } = await setup();
    fill(page);
    page.form.controls.amount.setValue(amount as number);
    await page.submit();
    expect(confirm.ask).not.toHaveBeenCalled();
    expect(api.transfer).not.toHaveBeenCalled();
  });

  it('blocks insufficient balance using the selected sender wallet', async () => {
    const { page, api } = await setup();
    fill(page, 101);
    await page.submit();
    expect(page.error()).toContain('the sender holds 100');
    page.form.patchValue({ sender: 'other', from_user_id: sender.id, amount: 41 });
    page.senderAccount.set(sender);
    await Promise.resolve();
    await page.submit();
    expect(page.error()).toContain('the sender holds 40');
    expect(api.transfer).not.toHaveBeenCalled();
  });

  it('sends from another account when selected and leaves remarks optional', async () => {
    const { page, api } = await setup();
    page.setOp('transfer');
    page.form.patchValue({ sender: 'other', from_user_id: sender.id, user_id: recipient.id, amount: 30, memo: '' });
    page.senderAccount.set(sender);
    page.recipient.set(recipient);
    await Promise.resolve();
    await page.submit();
    expect(api.transfer).toHaveBeenCalledWith({ to_user_id: 'recipient', from_user_id: 'sender', amount: 30, memo: null }, expect.any(String));
  });

  it('prevents transfers to the same wallet', async () => {
    const { page, api } = await setup();
    page.setOp('transfer');
    page.form.patchValue({ sender: 'other', from_user_id: recipient.id, user_id: recipient.id, amount: 5 });
    await Promise.resolve();
    await page.submit();
    expect(page.error()).toContain('must be different');
    expect(api.transfer).not.toHaveBeenCalled();
  });

  it('never posts a cancelled transfer or changes a balance optimistically', async () => {
    const { page, api, confirm } = await setup();
    fill(page);
    confirm.ask.mockResolvedValue(null);
    await page.submit();
    expect(api.transfer).not.toHaveBeenCalled();
    expect(page.summary()?.own_balance).toBe(100);
    expect(page.form.controls.amount.value).toBe(25);
    expect(page.form.enabled).toBe(true);
  });

  it('disables the form while posting and ignores duplicate submissions', async () => {
    const { page, api } = await setup();
    const response = new Subject<PointTransaction>();
    api.transfer.mockReturnValue(response);
    fill(page);
    const first = page.submit();
    await new Promise(resolve => setTimeout(resolve));
    expect(page.busy()).toBe(true);
    expect(page.form.disabled).toBe(true);
    await page.submit();
    await page.submit();
    expect(api.transfer).toHaveBeenCalledTimes(1);
    response.next(transaction); response.complete();
    await first;
    expect(page.busy()).toBe(false);
    expect(page.form.enabled).toBe(true);
  });

  it('shows a receipt and refetches balances after success', async () => {
    const { page, api, toast, text } = await setup();
    fill(page);
    const before = page.historyRefresh();
    await page.submit();
    expect(page.receipt()?.reference).toBe('tx-1');
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('Transfer completed'));
    expect(api.pointsSummary).toHaveBeenCalledTimes(2);
    expect(api.accountEntries).toHaveBeenCalledTimes(2);
    expect(page.historyRefresh()).toBe(before + 1);
    expect(page.form.controls.amount.value).toBeNull();
    expect(text()).toContain('tx-1');
  });

  it('shows API errors and issues a new key after a rejected request', async () => {
    const { page, api } = await setup();
    fill(page);
    api.transfer.mockReturnValueOnce(throwError(() => new HttpErrorResponse({
      status: 422, error: { error: { code: 'insufficient_points', message: 'Insufficient points', details: {}, request_id: null } },
    })));
    await page.submit();
    expect(page.error()).toBe('Insufficient points');
    await page.submit();
    expect(api.transfer.mock.calls[1][1]).not.toBe(api.transfer.mock.calls[0][1]);
  });

  it('keeps the idempotency key on an unchanged network retry', async () => {
    const { page, api } = await setup();
    fill(page);
    api.transfer.mockReturnValueOnce(throwError(() => new HttpErrorResponse({ status: 0 })));
    await page.submit();
    const key = api.transfer.mock.calls[0][1];
    expect(page.error()).toContain('retry safely');
    // The posting may have committed despite its lost response; a live refresh can show the reduced balance.
    page.summary.set({ ...page.summary()!, own_balance: 0 });
    await page.submit();
    expect(api.transfer.mock.calls[1][1]).toBe(key);
  });
});

describe('Permission-based visibility', () => {
  it.each(['operator', 'agent', 'master_agent', 'player'])('%s sees balances but no create or transfer controls', async role => {
    const { page, text } = await setup(role);
    expect(page.ops()).toEqual([]);
    expect(text()).not.toContain('Create Points');
    expect(page.canRefund()).toBe(false);
  });

  it('offers only the operations an Admin is permitted', async () => {
    const { page } = await setup('admin', ['points.transfer', 'points.view_all']);
    expect(page.ops()).toEqual(['transfer']);
    expect(page.canRefund()).toBe(false);
  });

  it('hides the other-sender option without points.view_all', async () => {
    const { page, text } = await setup('admin', ['points.transfer', 'points.allocate', 'points.view_downline']);
    page.setOp('transfer');
    expect(page.canViewAll()).toBe(false);
    expect(text()).not.toContain('Send from');
  });
});

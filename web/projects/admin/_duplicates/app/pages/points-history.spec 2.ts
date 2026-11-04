import { TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { MAT_DIALOG_DATA, MatDialog } from '@angular/material/dialog';
import { EMPTY, of, throwError } from 'rxjs';
import { ConfirmService } from '@shared/components';
import type { PointTransaction } from '@shared/models';
import { AdminApi, RealtimeService, ToastService } from '@shared/services';
import { PointsHistoryComponent } from './points-history';
import { TransactionDialogComponent } from './transaction-dialog';

const tx = {
  id: 'tx-1', reference: 'tx-1-reference', kind: 'transfer', type: 'transfer', direction: null, status: 'completed', amount: 25, memo: 'Float',
  source: { account_id: 'a1', kind: 'user', user_id: 'admin-id', username: 'admin', full_name: null },
  destination: { account_id: 'a2', kind: 'user', user_id: 'p1', username: 'player1', full_name: null },
  created_by_id: 'admin-id', created_by_username: 'admin', created_at: '2026-10-31T01:00:00Z', tenant_id: 't',
  source_balance_before: 100, source_balance_after: 75, destination_balance_before: 0, destination_balance_after: 25,
  reverses_transaction_id: null, refund_transaction_id: null,
} as PointTransaction;

async function setup(dialogResult: 'refund' | undefined = undefined) {
  const api = {
    transactions: vi.fn((_q: Record<string, unknown>) => of({ items: [tx], total: 60, page: 1, page_size: 25 })),
    exportTransactions: vi.fn((_q: Record<string, unknown>) => of(new Blob(['id\n']))),
    refund: vi.fn((_id: string, _reason: string, _key?: string) => of({ ...tx, id: 'tx-2', reference: 'tx-2-reference', kind: 'refund', type: 'refund' })),
    users: vi.fn(() => of({ items: [], total: 0, page: 1, page_size: 20 })),
    user: vi.fn((id: string) => of({ id, username: id, full_name: id, account_type: 'player', balance: 0 })),
  };
  const dialog = { open: vi.fn(() => ({ afterClosed: () => of(dialogResult) })) };
  const confirm = { ask: vi.fn<(...args: any[]) => Promise<string | null>>().mockResolvedValue('Wrong recipient') };
  TestBed.configureTestingModule({ providers: [
    { provide: AdminApi, useValue: api },
    { provide: MatDialog, useValue: dialog },
    { provide: ConfirmService, useValue: confirm },
    { provide: ToastService, useValue: { success: vi.fn() } },
    { provide: RealtimeService, useValue: { on: () => EMPTY } },
  ] });
  const fixture = TestBed.createComponent(PointsHistoryComponent);
  fixture.detectChanges();
  await fixture.whenStable();
  const last = () => api.transactions.mock.calls.at(-1)![0];
  return { fixture, history: fixture.componentInstance, api, dialog, confirm, last };
}

describe('Points history', () => {
  it('renders rows with type, parties, Manila time and status', async () => {
    const { fixture } = await setup();
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Transfer');
    expect(text).toContain('player1');
    expect(text).toContain('Oct 31, 2026, 9:00 AM');
    expect(text).toContain('Completed');
  });

  it('applies type, status, date and party filters and resets to page one', async () => {
    const { history, last } = await setup();
    history.list.setPage({ page: 3, pageSize: 25 });
    expect(last()).toMatchObject({ page: 3 });
    history.set('type', 'credit');
    history.set('status', 'reversed');
    history.set('date_from', '2026-10-01');
    history.set('date_to', '2026-10-31');
    history.sender.setValue('admin-id');
    history.recipient.setValue('p1');
    expect(last()).toMatchObject({ page: 1, type: 'credit', status: 'reversed', date_from: '2026-10-01', date_to: '2026-10-31', sender_id: 'admin-id', recipient_id: 'p1' });
    history.clear();
    expect(last()).toMatchObject({ type: null, status: null, sender_id: null, recipient_id: null });
    expect(history.hasFilters()).toBe(false);
  });

  it('searches by username, account ID or reference', async () => {
    const { history, last } = await setup();
    history.onSearch('tx-1-ref');
    expect(last()).toMatchObject({ q: 'tx-1-ref', page: 1 });
  });

  it('exports the filtered history as CSV', async () => {
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:points'), revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const { history, api } = await setup();
    history.set('type', 'transfer');
    history.onSearch('player1');
    history.export();
    expect(api.exportTransactions).toHaveBeenCalledWith(expect.objectContaining({ type: 'transfer', q: 'player1' }));
    expect(history.exporting()).toBe(false);
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });

  it('shows the server message when an export is refused', async () => {
    const { history, api } = await setup();
    const body = new Blob([JSON.stringify({ error: { code: 'export_too_large', message: 'Export exceeds 10,000 records.' } })]);
    api.exportTransactions.mockReturnValueOnce(throwError(() => new HttpErrorResponse({ status: 422, error: body })));
    history.export();
    await vi.waitFor(() => expect(history.error()).toBe('Export exceeds 10,000 records.'));
  });

  it('reloads when the page signals a refresh', async () => {
    const { fixture, api } = await setup();
    const before = api.transactions.mock.calls.length;
    fixture.componentRef.setInput('refresh', 1);
    fixture.detectChanges();
    expect(api.transactions.mock.calls.length).toBe(before + 1);
  });

  it('opens a read-only receipt even if an obsolete client requests a refund', async () => {
    const { history, api, dialog, confirm } = await setup('refund');
    history.open(tx);
    expect(dialog.open).toHaveBeenCalledWith(TransactionDialogComponent, expect.objectContaining({ data: { tx } }));
    expect(confirm.ask).not.toHaveBeenCalled();
    expect(api.refund).not.toHaveBeenCalled();
  });

});

describe('Transaction details dialog', () => {
  function render(data: { tx: PointTransaction; canRefund: boolean }) {
    TestBed.configureTestingModule({ providers: [{ provide: MAT_DIALOG_DATA, useValue: data }] });
    const fixture = TestBed.createComponent(TransactionDialogComponent);
    fixture.detectChanges();
    return fixture;
  }

  it('shows the full audit record', () => {
    const fixture = render({ tx, canRefund: true });
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    for (const value of ['tx-1-reference', 'admin', 'player1', '100', '75', 'Float', 'Asia/Manila']) expect(text).toContain(value);
    expect(fixture.nativeElement.querySelector('button').textContent).toContain('Close');
  });

  it.each([
    [{ ...tx, status: 'reversed' as const }, true],
    [{ ...tx, kind: 'allocation' as const, type: 'issue' as const }, true],
    [tx, false],
  ])('offers no refund when not applicable', (record, canRefund) => {
    const fixture = render({ tx: record, canRefund });
    expect(fixture.nativeElement.querySelectorAll('button')).toHaveLength(1);
    expect(fixture.nativeElement.textContent).not.toContain('Refund transfer');
  });
});

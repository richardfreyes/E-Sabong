import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { debounceTime } from 'rxjs';

import { ConfirmService, PageHeaderComponent } from '@shared/components';
import { formatArenaPoints, toCents } from '@shared/pipes';
import { BalanceRequestRow, CommissionApi, RealtimeService, ToastService } from '@shared/services';

/**
 * Requests from master agents and agents. A Loader or Super Admin approves or declines each one.
 */
@Component({
  selector: 'app-balance-requests',
  imports: [DatePipe, MatButtonModule, MatIconModule, PageHeaderComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .note { margin: 0 0 1rem; color: var(--text-secondary); font-size: 0.875rem; }
    .card { padding: 1rem; border-radius: 1rem; border: 1px solid var(--mat-sys-outline-variant); background: var(--surface); }
    ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
    li {
      display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: center; justify-content: space-between;
      padding: 0.75rem 0; border-top: 1px solid var(--mat-sys-outline-variant);
    }
    li:first-child { border-top: 0; padding-top: 0; }
    .who { font-weight: 700; }
    .meta { color: var(--text-secondary); font-size: 0.8rem; }
    .row-actions { display: inline-flex; gap: 0.25rem; flex-wrap: wrap; }
    .empty { margin: 0; color: var(--text-secondary); }
    .status { text-transform: capitalize; }
  `,
  template: `
    <app-page-header title="Balance Requests" subtitle="Approve or decline cashouts, earnings withdrawals, and balance requests." />
    <p class="note">Loaders and Super Admins are notified when a request arrives. A cashout or earnings withdrawal already left the account; declining puts it back. Approving a balance request adds the amount. A Loader approval spends that Loader's allocation.</p>
    <section class="card" aria-labelledby="balance-requests">
      <h2 id="balance-requests" class="sr-only">Balance requests</h2>
      @if (error()) {
        <p class="empty" role="alert">{{ error() }} <button type="button" (click)="reload()">Try again</button></p>
      } @else if (inbox().length === 0) {
        <p class="empty">No balance requests.</p>
      } @else {
        <ul>
          @for (row of inbox(); track row.id) {
            <li>
              <span>
                <span class="who">{{ row.requester_username }}</span>
                · {{ points(row.amount) }}
                · {{ label(row) }}
                · {{ row.created_at | date: 'medium' }}
                · <span class="status">{{ row.status }}</span>
              </span>
              @if (row.status === 'pending') {
                <span class="row-actions">
                  <button mat-flat-button type="button" (click)="decide(row, 'approve')">Approve</button>
                  <button mat-stroked-button type="button" (click)="decide(row, 'reject')">Reject</button>
                </span>
              }
            </li>
          }
        </ul>
      }
    </section>
  `,
})
export class BalanceRequestsPage {
  private readonly api = inject(CommissionApi);
  private readonly confirm = inject(ConfirmService);
  private readonly toast = inject(ToastService);
  private readonly destroy = inject(DestroyRef);

  readonly inbox = signal<BalanceRequestRow[]>([]);
  readonly error = signal<string | null>(null);

  constructor() {
    this.reload();
    inject(RealtimeService).on('notification.created', 'balance.updated').pipe(debounceTime(300), takeUntilDestroyed()).subscribe(() => this.reload());
  }

  reload(): void {
    this.api.approvals().pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: (rows) => { this.inbox.set(rows); this.error.set(null); },
      error: (err: unknown) => this.error.set(err instanceof Error ? err.message : 'Balance requests could not be loaded.'),
    });
  }

  points(value: string): string {
    return formatArenaPoints(toCents(value) ?? 0);
  }

  label(row: BalanceRequestRow): string {
    if (row.purpose === 'cashout') return 'Cashout main wallet';
    if (row.purpose === 'earnings') return 'Withdraw earnings';
    return 'Balance request';
  }

  async decide(row: BalanceRequestRow, decision: 'approve' | 'reject'): Promise<void> {
    const amount = this.points(row.amount);
    const who = row.requester_username;
    const ok = await this.confirm.ask({
      title: decision === 'approve' ? `Approve ${amount}?` : `Reject ${amount}?`,
      message: this.decisionMessage(row, decision, amount, who),
      confirmText: decision === 'approve' ? 'Approve' : 'Reject',
      destructive: decision === 'reject',
    });
    if (ok === null) return;
    this.api.decideBalanceRequest(row.id, decision).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: () => {
        this.toast.success(decision === 'approve' ? 'Request approved.' : 'Request declined.');
        this.reload();
      },
      error: (err: unknown) => this.toast.error(err),
    });
  }

  private decisionMessage(row: BalanceRequestRow, decision: 'approve' | 'reject', amount: string, who: string): string {
    if (row.purpose === 'cashout') {
      return decision === 'approve'
        ? `Confirm the cashout of ${amount} from ${who}. The amount already left their main wallet.`
        : `Decline the cashout. ${amount} returns to ${who}'s main wallet.`;
    }
    if (row.purpose === 'earnings') {
      return decision === 'approve'
        ? `Confirm the earnings withdrawal of ${amount} from ${who}.`
        : `Decline the withdrawal. ${amount} returns to ${who}'s available earnings.`;
    }
    return decision === 'approve'
      ? `Add ${amount} to ${who}.`
      : `Leave ${who}'s balance unchanged.`;
  }
}

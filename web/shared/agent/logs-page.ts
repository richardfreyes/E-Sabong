import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';
import { forkJoin } from 'rxjs';

import { ArenaPointsPipe } from '@shared/pipes';
import { BalanceRequestRow, CommissionApi, EarningsMoveRow, RealtimeService, toApiError } from '@shared/services';

/**
 * Cashout, earnings, and balance-request history. Opened from the agent menu.
 */
@Component({
  selector: 'app-agent-logs',
  imports: [MatIconModule, ArenaPointsPipe, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './logs-page.scss',
  templateUrl: './logs-page.html',
})
export class AgentLogsPage {
  private readonly api = inject(CommissionApi);
  private readonly destroy = inject(DestroyRef);

  readonly requests = signal<BalanceRequestRow[]>([]);
  readonly moves = signal<EarningsMoveRow[]>([]);
  readonly error = signal<string | null>(null);
  readonly logs = computed(() => {
    const rows = [
      ...this.requests().map((row) => ({
        id: row.id,
        at: row.created_at,
        amount: row.amount,
        status: row.status,
        label: row.purpose === 'cashout' ? 'Cashout main wallet' : row.purpose === 'earnings' ? 'Withdraw earnings' : 'Balance request',
      })),
      ...this.moves().map((row) => ({
        id: row.id,
        at: row.created_at,
        amount: row.amount,
        status: row.kind === 'withdrawal' ? 'approved' : 'merged',
        label: row.kind === 'withdrawal' ? 'Earnings withdrawn' : 'Earnings merged into main wallet',
      })),
    ];
    return rows.sort((a, b) => b.at.localeCompare(a.at));
  });

  constructor() {
    this.reload();
    inject(RealtimeService).on('balance.updated').pipe(takeUntilDestroyed()).subscribe(() => this.reload());
  }

  reload(): void {
    forkJoin({
      requests: this.api.balanceRequests(),
      moves: this.api.earningsMoves(),
    }).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: (data) => {
        this.requests.set(data.requests);
        this.moves.set(data.moves);
        this.error.set(null);
      },
      error: (err: unknown) => this.error.set(toApiError(err).message),
    });
  }
}

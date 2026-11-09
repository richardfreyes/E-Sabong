import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';
import { ActivatedRoute } from '@angular/router';
import { forkJoin } from 'rxjs';

import { ConfirmService } from '@shared/components';
import { ArenaPointsPipe } from '@shared/pipes';
import {
  CommissionApi, CommissionOverview, EarningsMoveRow,
  RealtimeService, ToastService, newIdempotencyKey, toApiError,
} from '@shared/services';

/**
 * Move available commission into this account's own play wallet.
 * Opened from the agent menu, not the dashboard cards.
 */
@Component({
  selector: 'app-agent-wallet',
  imports: [MatIconModule, ArenaPointsPipe, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './wallet-page.scss',
  templateUrl: './wallet-page.html',
})
export class AgentWalletPage {
  private readonly commissionApi = inject(CommissionApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly destroy = inject(DestroyRef);

  readonly overview = signal<CommissionOverview | null>(null);
  readonly moves = signal<EarningsMoveRow[]>([]);
  readonly error = signal<string | null>(null);
  readonly amount = signal('');
  readonly busy = signal(false);

  constructor() {
    inject(ActivatedRoute).data.pipe(takeUntilDestroyed()).subscribe(() => this.amount.set(''));
    this.reload();
    inject(RealtimeService).on<{ purpose: string; status: string }>('balance.updated').pipe(takeUntilDestroyed()).subscribe((event) => {
      this.reload();
      const decided = event?.status === 'approved' ? 'approved' : event?.status === 'rejected' ? 'declined' : '';
      if (!decided) return;
      const what = event.purpose === 'cashout' ? 'Cashout' : event.purpose === 'earnings' ? 'Earnings withdrawal' : 'Balance request';
      this.toast.info(`${what} ${decided}. Your balance is updated.`);
    });
  }

  reload(): void {
    forkJoin({
      overview: this.commissionApi.overview(),
      moves: this.commissionApi.earningsMoves(),
    }).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: (data) => {
        this.overview.set(data.overview);
        this.moves.set(data.moves);
        this.error.set(null);
      },
      error: (err: unknown) => this.error.set(toApiError(err).message),
    });
  }

  fillMax(): void {
    const available = this.overview()?.available_earnings;
    if (available) this.amount.set(available);
  }

  async submit(): Promise<void> {
    const amount = this.amount().trim();
    if (!this.validAmount(amount) || this.busy()) return;
    await this.sendTransfer(amount);
  }

  private async sendTransfer(amount: string): Promise<void> {
    const available = Number(this.overview()?.available_earnings ?? '0');
    if (Number(amount) > available) {
      this.toast.error('That amount is more than the available commission earnings.');
      return;
    }
    const accepted = await this.confirm.ask({
      title: 'Transfer to play balance',
      message: `Move ${amount} from available earnings into your play balance? Earnings go down by the same amount.`,
      confirmText: 'Transfer',
    });
    if (accepted === null) return;
    this.busy.set(true);
    this.commissionApi.moveEarnings({ amount, idempotency_key: newIdempotencyKey('earnings-move') }).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: () => {
        this.busy.set(false);
        this.amount.set('');
        this.toast.success('Earnings moved to your play balance.');
        this.reload();
      },
      error: (err: unknown) => { this.busy.set(false); this.toast.error(toApiError(err).message); },
    });
  }

  private validAmount(value: string): boolean {
    if (!/^\d+(\.\d{1,2})?$/.test(value) || Number(value) <= 0 || Number(value) > 1_000_000) {
      this.toast.error('Enter an amount greater than 0, with at most 2 decimal places, up to 1,000,000.');
      return false;
    }
    return true;
  }
}

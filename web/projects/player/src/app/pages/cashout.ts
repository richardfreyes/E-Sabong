import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';

import { ArenaPointsPipe } from '@shared/pipes';
import { InboxItem, PortalApi, RealtimeService, ToastService, newIdempotencyKey, toApiError } from '@shared/services';
import { PlayWalletStore } from '../core/play-betting';
import { PageHeadComponent } from '../ui/page-head';

/** The player asks their agent to take play points back. The agent accepts or denies it from My Inbox. */
@Component({
  selector: 'app-player-cashout',
  imports: [DatePipe, MatButtonModule, MatIconModule, ArenaPointsPipe, PageHeadComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .card { padding: 1.25rem 1.4rem; border-radius: 1.25rem; border: 1px solid var(--pp-rope); background: var(--pp-pit); max-width: 36rem; }
    .balance { margin: 0 0 1rem; color: var(--pp-ash); }
    .balance strong { color: var(--pp-chalk); font-size: 1.6rem; }
    form { display: grid; gap: 0.75rem; }
    label { display: grid; gap: 0.35rem; color: var(--pp-ash); font-size: 0.8rem; }
    input { min-height: 3rem; padding: 0 0.8rem; border-radius: 0.6rem; border: 1px solid var(--pp-rope); background: var(--pp-pit-raised); color: var(--pp-chalk); font: inherit; font-size: 1.25rem; }
    .note, .waiting { margin: 0.85rem 0 0; color: var(--pp-ash); font-size: 0.85rem; }
    .form-error { margin: 0; color: var(--mat-sys-error); }
    ul { list-style: none; margin: 1.25rem 0 0; padding: 0; max-width: 36rem; }
    li { display: flex; justify-content: space-between; gap: 1rem; padding: 0.7rem 0; border-top: 1px solid var(--pp-rope); color: var(--pp-ash); }
    li strong { color: var(--pp-chalk); }
  `,
  template: `
    <pp-page-head title="Cashout request" subtitle="Enter the play points you want your agent to take back. The request waits in your agent's inbox until they accept or deny it." />
    <section class="card" aria-label="Cashout request">
      <p class="balance">Available balance <strong class="pp-num" data-field="balance">{{ wallet.balance() | arenaPoints }}</strong></p>
      @if (pending(); as open) {
        <p class="waiting" data-field="open-request">Waiting for {{ open.upline_username }} to accept {{ open.amount | arenaPoints }}.</p>
      } @else {
        <form (submit)="submit($event)">
          <label>Amount
            <input type="text" inputmode="decimal" autocomplete="off" aria-label="Amount" [value]="amount()" (input)="amount.set($any($event.target).value)" data-field="cashout-amount" />
          </label>
          @if (error()) { <p class="form-error" role="alert">{{ error() }}</p> }
          <button mat-flat-button type="submit" [disabled]="busy()" data-action="request-cashout">Request cashout</button>
        </form>
      }
      <p class="note">This returns play points to your agent. Earnings are not included, and nothing is paid as cash.</p>
    </section>
    @if (history().length) {
      <ul aria-label="Recent requests">
        @for (row of history(); track row.id) {
          <li><span>{{ row.created_at | date: 'medium' }}</span><strong>{{ row.amount | arenaPoints }} · {{ row.status }}</strong></li>
        }
      </ul>
    }
  `,
})
export class CashoutRequestPage {
  private readonly api = inject(PortalApi);
  private readonly toast = inject(ToastService);
  private readonly destroyRef = inject(DestroyRef);
  readonly wallet = inject(PlayWalletStore);
  readonly amount = signal('');
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);
  readonly pending = signal<InboxItem | null>(null);
  readonly history = signal<InboxItem[]>([]);
  private requestKey = newIdempotencyKey('inbox');

  constructor() {
    this.wallet.load();
    this.load();
    inject(RealtimeService).on('inbox.updated', 'wallet.updated').pipe(takeUntilDestroyed()).subscribe(() => this.load());
  }

  load(): void {
    this.api.inbox().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (rows) => {
        this.history.set(rows);
        this.pending.set(rows.find((row) => row.status === 'pending') ?? null);
      },
      error: () => this.history.set([]),
    });
  }

  submit(event: Event): void {
    event.preventDefault();
    const amount = this.amount().trim();
    if (this.busy()) return;
    if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0) {
      this.error.set('Enter an amount above zero.');
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.api.requestInbox({ amount: Number(amount).toFixed(2), idempotency_key: this.requestKey }).subscribe({
      next: (row) => {
        this.busy.set(false);
        this.pending.set(row);
        this.amount.set('');
        this.requestKey = newIdempotencyKey('inbox');
        this.toast.success('Request sent. It is waiting for your agent to accept.');
        this.load();
      },
      error: (e: unknown) => { this.busy.set(false); this.error.set(toApiError(e).message); },
    });
  }
}

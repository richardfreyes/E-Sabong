import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';

import { ArenaPointsPipe } from '@shared/pipes';
import { AgentUsersApi, InboxCounts, InboxItem, RealtimeService, ToastService, toApiError } from '@shared/services';

/**
 * My Inbox, after the SWC V2 agent reference. A player asks for play points to go back to the
 * direct upline. Approving moves those points. It does not pay cash, and earnings are not used.
 */
@Component({
  selector: 'app-agent-inbox',
  imports: [DatePipe, MatIconModule, ArenaPointsPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './inbox.scss',
  templateUrl: './inbox.html',
})
export class AgentInboxPage {
  private readonly api = inject(AgentUsersApi);
  private readonly toast = inject(ToastService);
  private readonly destroyRef = inject(DestroyRef);

  readonly tab = signal<'cashout' | 'commission'>('cashout');
  readonly items = signal<InboxItem[] | null>(null);
  readonly counts = signal<InboxCounts>({ cashout: 0, commission: 0 });
  readonly busy = signal<string | null>(null);
  readonly error = signal<string | null>(null);

  constructor() {
    this.reload();
    inject(RealtimeService).on('inbox.updated').pipe(takeUntilDestroyed()).subscribe(() => this.reload());
  }

  reload(): void {
    this.api.inbox().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (rows) => this.items.set(rows),
      error: (e: unknown) => { this.items.set([]); this.error.set(toApiError(e).message); },
    });
    this.api.inboxCounts().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (counts) => this.counts.set(counts),
      error: () => undefined,
    });
  }

  decide(row: InboxItem, decision: 'approve' | 'deny'): void {
    if (this.busy()) return;
    this.busy.set(row.id);
    this.error.set(null);
    this.api.decide(row.id, decision).subscribe({
      next: (result) => {
        this.busy.set(null);
        this.toast.success(result.status === 'approved'
          ? `Accepted. ${result.amount} play points returned from ${result.player_username}.`
          : `Request from ${result.player_username} denied.`);
        this.reload();
      },
      error: (e: unknown) => { this.busy.set(null); this.error.set(toApiError(e).message); },
    });
  }
}

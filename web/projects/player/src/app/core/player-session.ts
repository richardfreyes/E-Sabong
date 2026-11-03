import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import type { AppNotification } from '@shared/models';
import { PortalApi, RealtimeService } from '@shared/services';

/**
 * Notification counters shown in the top bar, kept fresh over the WebSocket. The only balance the
 * player sees is the play balance (PlayWalletStore); the retired points module has no player view.
 */
@Injectable({ providedIn: 'root' })
export class PlayerSession {
  private readonly api = inject(PortalApi);
  readonly unread = signal(0);
  readonly recent = signal<AppNotification[]>([]);

  constructor() {
    const realtime = inject(RealtimeService);
    const destroy = inject(DestroyRef);
    realtime.on<AppNotification>('notification.created').pipe(takeUntilDestroyed(destroy)).subscribe((n) => {
      this.unread.update((c) => c + 1);
      this.recent.update((list) => [{ ...n, data: n.data ?? {}, read_at: null, created_at: new Date().toISOString() }, ...list].slice(0, 6));
    });
  }

  load(): void {
    this.refreshUnread();
  }

  refreshUnread(): void {
    this.api.notifications({ unread_only: true, page_size: 1 }).subscribe({ next: (p) => this.unread.set(p.total), error: () => undefined });
  }

  loadRecent(): void {
    this.api.notifications({ page_size: 6 }).subscribe({ next: (p) => this.recent.set(p.items), error: () => undefined });
  }

  markAllRead(): void {
    this.api.readAll().subscribe(() => {
      this.unread.set(0);
      this.recent.update((list) => list.map((n) => ({ ...n, read_at: n.read_at ?? new Date().toISOString() })));
    });
  }
}

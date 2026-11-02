import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { EMPTY, Subject, catchError, switchMap } from 'rxjs';

import type { AppNotification } from '@shared/models';
import { BrandingService, PortalApi, RealtimeService, watchPoints } from '@shared/services';

/** Balance and notification counters shown in the top bar, kept fresh over the WebSocket. */
@Injectable({ providedIn: 'root' })
export class PlayerSession {
  private readonly refreshBalance = new Subject<void>();
  private readonly api = inject(PortalApi);
  private readonly branding = inject(BrandingService);
  readonly balance = signal<number | null>(null);
  readonly unread = signal(0);
  readonly recent = signal<AppNotification[]>([]);
  /** Bumped when points arrive, so the balance can briefly highlight. */
  readonly balancePulse = signal(0);

  constructor() {
    const realtime = inject(RealtimeService);
    const destroy = inject(DestroyRef);
    this.refreshBalance.pipe(
      switchMap(() => this.api.points().pipe(catchError(() => EMPTY))), takeUntilDestroyed(destroy),
    ).subscribe(account => {
      if (account.balance !== this.balance()) this.balancePulse.update(count => count + 1);
      this.balance.set(account.balance);
    });
    watchPoints(() => { if (this.branding.hasModule('points')) this.refreshBalance.next(); });
    realtime.on<AppNotification>('notification.created').pipe(takeUntilDestroyed(destroy)).subscribe((n) => {
      this.unread.update((c) => c + 1);
      this.recent.update((list) => [{ ...n, data: n.data ?? {}, read_at: null, created_at: new Date().toISOString() }, ...list].slice(0, 6));
    });
  }

  load(): void {
    if (this.branding.hasModule('points')) {
      this.refreshBalance.next();
    }
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

import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatPaginatorModule } from '@angular/material/paginator';

import type { AppNotification } from '@shared/models';
import { RelativeTimePipe } from '@shared/pipes';
import { PagedList, PortalApi, ToastService } from '@shared/services';
import { PlayerSession } from '../core/player-session';
import { PageHeadComponent } from '../ui/page-head';

@Component({
  selector: 'app-player-notifications',
  imports: [MatButtonModule, MatIconModule, MatPaginatorModule, RelativeTimePipe, PageHeadComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    ul { list-style: none; margin: 0; padding: 0; }
    li + li { border-top: 1px solid var(--pp-rope); }
    button.item { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 0.85rem; width: 100%; padding: 0.9rem 1.1rem; border: 0;
      background: transparent; color: inherit; text-align: left; font: inherit; cursor: pointer; }
    button.item:hover { background: var(--pp-pit-raised); }
    .icon { display: grid; place-items: center; width: 2.25rem; height: 2.25rem; border-radius: 50%; background: var(--pp-pit-raised); color: var(--pp-ash); }
    .unread .icon { color: var(--mat-sys-primary); background: color-mix(in srgb, var(--mat-sys-primary) 14%, transparent); }
    .title { display: block; color: var(--pp-chalk); }
    .unread .title { font-weight: 600; }
    .body { display: block; color: var(--pp-ash); font-size: 0.85rem; margin-top: 0.15rem; }
    time { color: var(--pp-ash); font-size: 0.78rem; white-space: nowrap; }
    .empty { padding: 2rem 1.1rem; color: var(--pp-ash); text-align: center; }
  `,
  template: `
    <pp-page-head title="Notifications">
      @if (session.unread()) {
        <button mat-stroked-button type="button" (click)="readAll()"><mat-icon>done_all</mat-icon>Mark all as read</button>
      }
    </pp-page-head>
    <section class="pp-panel overflow-hidden">
      @if (list.error()) {
        <p class="empty" role="alert">Notifications didn't load: {{ list.error() }}</p>
      } @else {
        <ul>
          @for (n of list.rows(); track n.id) {
            <li [class.unread]="!n.read_at">
              <button type="button" class="item" (click)="read(n)" [attr.aria-label]="n.title + (n.read_at ? '' : ', unread')">
                <span class="icon"><mat-icon aria-hidden="true">{{ n.type === 'points.received' ? 'toll' : 'notifications' }}</mat-icon></span>
                <span><span class="title">{{ n.title }}</span>@if (n.body) {<span class="body">{{ n.body }}</span>}</span>
                <time>{{ n.created_at | relativeTime }}</time>
              </button>
            </li>
          } @empty {
            @if (!list.loading()) { <li class="empty">You're all caught up. New points and announcements show up here.</li> }
          }
        </ul>
      }
    </section>
    @if (list.total() > list.pageSize()) {
      <mat-paginator class="mt-3" [length]="list.total()" [pageIndex]="list.page() - 1" [pageSize]="list.pageSize()"
                     (page)="list.setPage({ page: $event.pageIndex + 1, pageSize: $event.pageSize })" aria-label="Select page" />
    }
  `,
})
export class NotificationsPage {
  private readonly api = inject(PortalApi);
  private readonly toast = inject(ToastService);
  readonly session = inject(PlayerSession);
  readonly list = new PagedList<AppNotification>((q) => this.api.notifications(q));

  constructor() {
    this.list.load();
  }

  read(n: AppNotification): void {
    if (n.read_at) return;
    this.api.readNotification(n.id).subscribe(() => {
      this.list.load();
      this.session.refreshUnread();
    });
  }

  readAll(): void {
    this.api.readAll().subscribe({
      next: (m) => {
        this.toast.info(m.message);
        this.list.load();
        this.session.refreshUnread();
      },
      error: (e) => this.toast.error(e),
    });
  }
}

import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MatPaginatorModule } from '@angular/material/paginator';

import type { ActivityItem } from '@shared/models';
import { PagedList, PortalApi } from '@shared/services';
import { PageHeadComponent } from '../ui/page-head';

const ACTION_LABELS: Record<string, string> = {
  'auth.login': 'Signed in',
  'auth.login_failed': 'Failed sign-in attempt',
  'auth.password_changed': 'Password changed',
  'profile.updated': 'Profile updated',
  'user.created': 'Account created',
  'user.updated': 'Account details changed by an administrator',
  'user.password_reset': 'Password reset by an administrator',
  'user.suspended': 'Account suspended',
  'user.activated': 'Account reactivated',
};

@Component({
  selector: 'app-player-activity',
  imports: [DatePipe, MatPaginatorModule, PageHeadComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    ul { list-style: none; margin: 0; padding: 0; }
    li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0.25rem 1rem; padding: 0.8rem 1.1rem; }
    li + li { border-top: 1px solid var(--pp-rope); }
    .what { color: var(--pp-chalk); }
    .warn { color: var(--warning-text); }
    .meta { color: var(--pp-ash); font-size: 0.8rem; font-variant-numeric: tabular-nums; }
    .empty { color: var(--pp-ash); }
  `,
  template: `
    <pp-page-head title="Account activity" subtitle="Sign-ins and changes to your account. Tell your agent about anything you don't recognise." />
    <section class="pp-panel overflow-hidden">
      <ul>
        @for (a of list.rows(); track a.id) {
          <li>
            <span class="what" [class.warn]="a.action === 'auth.login_failed'">{{ label(a.action) }}</span>
            <span class="meta">{{ a.created_at | date: 'MMM d, y, h:mm a' }}@if (a.ip) {, from {{ a.ip }}}</span>
          </li>
        } @empty {
          @if (!list.loading()) { <li class="empty">No activity yet.</li> }
        }
      </ul>
    </section>
    @if (list.total() > list.pageSize()) {
      <mat-paginator class="mt-3" [length]="list.total()" [pageIndex]="list.page() - 1" [pageSize]="list.pageSize()"
                     (page)="list.setPage({ page: $event.pageIndex + 1, pageSize: $event.pageSize })" aria-label="Select page" />
    }
  `,
})
export class ActivityPage {
  private readonly api = inject(PortalApi);
  readonly list = new PagedList<ActivityItem>((q) => this.api.activity(q));
  readonly label = (action: string) => ACTION_LABELS[action] ?? action;

  constructor() {
    this.list.load();
  }
}

import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';
import { MatPaginatorModule } from '@angular/material/paginator';

import type { LedgerEntry, PointAccount } from '@shared/models';
import { LabelPipe, PointsPipe } from '@shared/pipes';
import { PagedList, PortalApi, RealtimeService } from '@shared/services';
import { PageHeadComponent } from '../ui/page-head';

@Component({
  selector: 'app-player-points',
  imports: [DatePipe, MatIconModule, MatPaginatorModule, LabelPipe, PointsPipe, PageHeadComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .hero { display: grid; grid-template-columns: auto 1fr; gap: 1.5rem; align-items: center; padding: 1.4rem 1.5rem; margin-bottom: 1rem;
      border-radius: 1.25rem; border: 1px solid color-mix(in srgb, var(--mat-sys-primary) 35%, var(--pp-rope));
      background: radial-gradient(ellipse 60% 120% at 0% 0%, color-mix(in srgb, var(--mat-sys-primary) 16%, transparent), transparent 70%), var(--pp-pit); }
    .label { margin: 0; color: var(--pp-ash); font-size: 0.85rem; }
    .balance { margin: 0.4rem 0 0; font-size: clamp(3rem, 2.4rem + 3vw, 4.75rem); color: var(--pp-chalk); }
    .note { display: flex; gap: 0.6rem; margin: 0; color: var(--pp-ash); font-size: 0.875rem; max-width: 46ch; justify-self: end; }
    .note mat-icon { flex: none; color: var(--mat-sys-primary); }
    ul { list-style: none; margin: 0; padding: 0; }
    li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0.25rem 1rem; padding: 0.75rem 1.1rem; }
    li + li { border-top: 1px solid var(--pp-rope); }
    .what { color: var(--pp-chalk); font-weight: 500; }
    .meta { color: var(--pp-ash); font-size: 0.8rem; }
    .delta { font-size: 1.4rem; text-align: right; color: var(--pp-chalk); }
    .delta.out { color: var(--pp-ash); }
    .after { text-align: right; color: var(--pp-ash); font-size: 0.8rem; font-variant-numeric: tabular-nums; }
    .empty { padding: 1.25rem 1.1rem; color: var(--pp-ash); }
    .skeleton { height: 3.5rem; margin: 0.75rem 1.1rem; border-radius: 0.6rem; background: var(--pp-pit-raised); animation: pp-pulse 1.6s ease-in-out infinite; }
    @media (max-width: 767px) { .hero { grid-template-columns: 1fr; gap: 0.75rem; } .note { justify-self: start; } }
  `,
  template: `
    <pp-page-head title="Points history" />
    <section class="hero" aria-label="Demonstration points balance">
      <div>
        <p class="label">Current balance</p>
        <p class="balance pp-num">{{ account()?.balance | points }}</p>
      </div>
      <p class="note"><mat-icon aria-hidden="true">info</mat-icon>{{ account()?.disclaimer ?? 'Demonstration points have no monetary value and cannot be redeemed.' }}</p>
    </section>
    <section class="pp-panel overflow-hidden" aria-labelledby="statement">
      <div class="pp-panel-head"><h2 id="statement" class="pp-h2">Statement</h2></div>
      @if (list.loading() && !list.rows().length) {
        @for (i of [1, 2, 3]; track i) { <div class="skeleton"></div> }
      } @else {
        <ul>
          @for (e of list.rows(); track e.id) {
            <li>
              <span class="what">{{ e.delta > 0 ? 'Received from ' : 'Sent to ' }}{{ e.counterparty?.username ?? e.counterparty?.full_name ?? 'the organiser' }}</span>
              <span class="pp-num delta" [class.out]="e.delta < 0">{{ e.delta | points: true }}</span>
              <span class="meta">{{ e.kind | label }}, {{ e.created_at | date: 'MMM d, y, h:mm a' }}@if (e.memo) {: {{ e.memo }}}</span>
              <span class="after">Balance {{ e.balance_after | points }}</span>
            </li>
          } @empty {
            <li class="empty">No points yet. Points you receive from your agent appear here.</li>
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
export class PointsPage {
  private readonly api = inject(PortalApi);
  readonly account = signal<PointAccount | null>(null);
  readonly list = new PagedList<LedgerEntry>((q) => this.api.pointsHistory(q));

  constructor() {
    this.load();
    inject(RealtimeService).on('points.received').pipe(takeUntilDestroyed(inject(DestroyRef))).subscribe(() => this.load());
  }

  load(): void {
    this.api.points().subscribe((a) => this.account.set(a));
    this.list.load();
  }
}

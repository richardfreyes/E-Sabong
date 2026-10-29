import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';

import { CellDefDirective, Column, DataTableComponent, EmptyStateComponent, PageHeaderComponent, StatusChipComponent } from '@shared/components';
import type { Invoice, Subscription } from '@shared/models';
import { MoneyPipe } from '@shared/pipes';
import { AdminApi, BrandingService, PagedList } from '@shared/services';

@Component({
  selector: 'app-admin-subscription',
  imports: [PageHeaderComponent, StatusChipComponent, EmptyStateComponent, DataTableComponent, CellDefDirective, DatePipe, MoneyPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Subscription" subtitle="Your software plan and invoices. Contact your provider to change plans." />
    @if (sub(); as s) {
      <section class="mb-6 grid gap-4 rounded-2xl border border-outline-variant p-4 md:grid-cols-3">
        <div><p class="m-0 text-xs text-on-surface-variant">Plan</p><p class="m-0 mt-1 text-xl font-semibold">{{ s.plan.name }}</p><app-status-chip [status]="s.status" /></div>
        <div><p class="m-0 text-xs text-on-surface-variant">{{ s.status === 'trialing' ? 'Trial ends' : 'Current period ends' }}</p>
          <p class="m-0 mt-1 text-xl font-semibold">{{ (s.status === 'trialing' ? s.trial_ends_at : s.current_period_end) | date: 'mediumDate' }}</p>
          @if (s.cancel_at_period_end) { <p class="m-0 text-sm text-error">Cancels at the end of this period</p> }</div>
        <div><p class="m-0 text-xs text-on-surface-variant">Price</p>
          <p class="m-0 mt-1 text-xl font-semibold">{{ (s.billing_cycle === 'annual' ? s.plan.price_annual_cents : s.plan.price_monthly_cents) | money: s.plan.currency }}
            <span class="text-sm font-normal text-on-surface-variant">/ {{ s.billing_cycle === 'annual' ? 'year' : 'month' }}</span></p>
          <p class="m-0 text-xs text-on-surface-variant">Includes: {{ s.plan.modules.join(', ') }}</p></div>
        @if (s.status === 'past_due') {
          <p class="m-0 rounded-lg bg-error-container p-3 text-sm text-on-error-container md:col-span-3" role="alert">
            Payment is overdue. The workspace is suspended automatically after the grace period. Contact {{ contact() }}.</p>
        }
      </section>
    } @else if (loaded()) {
      <app-empty-state icon="verified" title="Licensed installation" message="This workspace runs under a software license rather than a subscription." />
    }
    <h2 class="m-0 mb-3 text-base font-semibold">Invoices</h2>
    <app-data-table [columns]="columns" [rows]="invoices.rows()" [total]="invoices.total()" [page]="invoices.page()" [pageSize]="invoices.pageSize()"
                    [loading]="invoices.loading()" [error]="invoices.error()" [searchable]="false" emptyTitle="No invoices yet" emptyIcon="receipt_long"
                    (pageChange)="invoices.setPage($event)" (retry)="invoices.load()">
      <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
      <ng-template appCell="total_cents" let-row>{{ row.total_cents | money: row.currency }}</ng-template>
      <ng-template appCell="issued_at" let-row>{{ row.issued_at | date: 'mediumDate' }}</ng-template>
      <ng-template appCell="due_at" let-row>{{ row.due_at | date: 'mediumDate' }}</ng-template>
    </app-data-table>
  `,
})
export class SubscriptionPage {
  private readonly api = inject(AdminApi);
  private readonly branding = inject(BrandingService);
  readonly sub = signal<Subscription | null>(null);
  readonly loaded = signal(false);
  readonly contact = () => this.branding.config()?.branding.contact_email ?? 'your provider';
  readonly invoices = new PagedList<Invoice>((q) => this.api.invoices(q));
  readonly columns: Column<Invoice>[] = [
    { key: 'number', label: 'Number' },
    { key: 'status', label: 'Status' },
    { key: 'total_cents', label: 'Total', align: 'end' },
    { key: 'issued_at', label: 'Issued', hideOnMobile: true },
    { key: 'due_at', label: 'Due', hideOnMobile: true },
  ];

  constructor() {
    this.api.subscription().subscribe({ next: (s) => (this.sub.set(s), this.loaded.set(true)), error: () => this.loaded.set(true) });
    this.invoices.load();
  }
}

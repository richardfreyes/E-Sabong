import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { Router } from '@angular/router';

import { CellDefDirective, Column, DataTableComponent, PageHeaderComponent, StatusChipComponent } from '@shared/components';
import type { Subscription } from '@shared/models';
import { PagedList, PlatformApi } from '@shared/services';

@Component({
  selector: 'app-owner-subscriptions',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, StatusChipComponent, MatButtonToggleModule, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Subscriptions" subtitle="Plan changes, cancellations and renewals are managed on each tenant." />
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                    [loading]="list.loading()" [error]="list.error()" [filters]="true" [rowClickable]="true" searchPlaceholder="Search tenant"
                    emptyTitle="No subscriptions" emptyIcon="autorenew" (search)="list.setSearch($event)" (pageChange)="list.setPage($event)"
                    (retry)="list.load()" (rowClick)="open($event)">
      <mat-button-toggle-group filters [value]="status()" (change)="setStatus($event.value)" aria-label="Filter by status" hideSingleSelectionIndicator>
        <mat-button-toggle value="">All</mat-button-toggle><mat-button-toggle value="trialing">Trialing</mat-button-toggle>
        <mat-button-toggle value="active">Active</mat-button-toggle><mat-button-toggle value="past_due">Past due</mat-button-toggle>
        <mat-button-toggle value="expired">Expired</mat-button-toggle>
      </mat-button-toggle-group>
      <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
      <ng-template appCell="period" let-row>{{ row.current_period_start | date: 'mediumDate' }} – {{ row.current_period_end | date: 'mediumDate' }}</ng-template>
      <ng-template appCell="flags" let-row>
        @if (row.cancel_at_period_end) { <span class="text-xs text-error">cancels at period end</span> }
        @if (row.pending_plan) { <span class="text-xs">→ {{ row.pending_plan.name }}</span> }
      </ng-template>
    </app-data-table>
  `,
})
export class SubscriptionsPage {
  private readonly api = inject(PlatformApi);
  private readonly router = inject(Router);
  readonly status = signal('');
  readonly list = new PagedList<Subscription>((q) => this.api.subscriptions(q));
  readonly columns: Column<Subscription>[] = [
    { key: 'tenant_name', label: 'Tenant' },
    { key: 'plan', label: 'Plan', cell: (s) => s.plan.name },
    { key: 'billing_cycle', label: 'Cycle', hideOnMobile: true },
    { key: 'status', label: 'Status' },
    { key: 'period', label: 'Current period', hideOnMobile: true },
    { key: 'flags', label: '', hideOnMobile: true },
  ];

  constructor() {
    this.list.load();
  }

  setStatus(value: string): void {
    this.status.set(value);
    this.list.setFilter('status', value || null);
  }

  open(s: Subscription): void {
    void this.router.navigate(['/tenants', s.tenant_id]);
  }
}

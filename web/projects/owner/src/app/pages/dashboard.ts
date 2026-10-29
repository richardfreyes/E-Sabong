import { KeyValuePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';

import { BarChartComponent, ErrorStateComponent, PageHeaderComponent, StatCardComponent, StatusChipComponent } from '@shared/components';
import type { PlatformOverview } from '@shared/models';
import { LabelPipe, MoneyPipe } from '@shared/pipes';
import { PlatformApi, toApiError } from '@shared/services';

@Component({
  selector: 'app-owner-dashboard',
  imports: [BarChartComponent, PageHeaderComponent, StatCardComponent, StatusChipComponent, ErrorStateComponent, RouterLink, MatButtonModule, KeyValuePipe, LabelPipe],
  providers: [MoneyPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header eyebrow="Platform operations" title="Platform overview" subtitle="Live state of every tenant, subscription and deployment.">
      <a mat-stroked-button routerLink="/invoices">View invoices</a><a mat-flat-button routerLink="/tenants">Manage tenants</a>
    </app-page-header>

    @if (error()) {
      <app-error-state [message]="error()!" (retry)="load()" />
    } @else {
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <app-stat-card tone="success" label="Active tenants" icon="domain" [loading]="!data()" [value]="data()?.tenants_by_status?.active ?? null"
                       [hint]="data() ? data()!.new_tenants_30d + ' new in the last 30 days' : null" />
        <app-stat-card tone="accent" label="Monthly recurring revenue" icon="payments" [loading]="!data()" [value]="mrr()" hint="Active and past-due subscriptions" />
        <app-stat-card tone="warning" label="Trials ending in 7 days" icon="hourglass_bottom" [loading]="!data()" [value]="data()?.trials_ending_7d ?? null" />
        <app-stat-card tone="warning" label="Overdue invoices" icon="warning" [loading]="!data()" [value]="data()?.overdue_invoices ?? null"
                       [hint]="data() ? data()!.open_invoices + ' open in total' : null" />
        <app-stat-card label="Revenue, last 30 days" icon="account_balance" [loading]="!data()" [value]="revenue()" />
        <app-stat-card label="Accounts across tenants" icon="group" [loading]="!data()" [value]="data()?.total_users?.toLocaleString() ?? null" />
        <app-stat-card label="Dedicated deployments" icon="dns" [loading]="!data()" [value]="data()?.dedicated_deployments ?? null" />
        <app-stat-card label="API requests, last 30 days" icon="speed" [loading]="!data()" [value]="data()?.usage_30d?.['api_requests']?.toLocaleString() ?? '0'" />
      </div>

      @if (data(); as d) {
        <div class="mt-6 grid gap-4 lg:grid-cols-2">
          <section class="arena-panel">
            <p class="arena-eyebrow mb-5">Customer portfolio</p>
            <app-bar-chart title="Tenants by status" unit="tenants" [data]="tenantSeries()" />
            <ul class="arena-list mt-4">
              @for (s of d.tenants_by_status | keyvalue; track s.key) {
                @if (s.value) { <li><app-status-chip [status]="s.key" /><strong class="tabular-nums">{{ s.value }}</strong></li> }
              }
            </ul>
          </section>
          <section class="arena-panel">
            <p class="arena-eyebrow mb-5">Subscription lifecycle</p>
            <app-bar-chart title="Subscriptions by status" color="secondary" unit="subscriptions" [data]="subscriptionSeries()" />
            <ul class="arena-list mt-4">
              @for (s of d.subscriptions_by_status | keyvalue; track s.key) {
                @if (s.value) { <li><app-status-chip [status]="s.key" /><strong class="tabular-nums">{{ s.value }}</strong></li> }
              } @empty { <li class="text-on-surface-variant">No subscriptions yet.</li> }
            </ul>
          </section>
          <section class="arena-panel lg:col-span-2">
            <div class="arena-section-head"><div><h2>Usage across tenants</h2><p>Recorded activity over the last 30 days</p></div><a routerLink="/deployments" class="text-xs text-primary no-underline">View deployments</a></div>
            <dl class="m-0 grid grid-cols-2 gap-5 sm:grid-cols-3">
              @for (u of d.usage_30d | keyvalue; track u.key) {
                <div><dt class="text-xs text-on-surface-variant">{{ u.key | label }}</dt><dd class="m-0 text-lg font-semibold tabular-nums">{{ u.value.toLocaleString() }}</dd></div>
              } @empty {
                <p class="m-0 text-sm text-on-surface-variant">Usage is collected every 10 minutes by the worker.</p>
              }
            </dl>
          </section>
        </div>
      }
    }
  `,
})
export class DashboardPage {
  private readonly api = inject(PlatformApi);
  private readonly money = inject(MoneyPipe);
  readonly data = signal<PlatformOverview | null>(null);
  readonly error = signal<string | null>(null);
  private readonly labels = new LabelPipe();
  readonly tenantSeries = computed(() => Object.entries(this.data()?.tenants_by_status ?? {}).filter(([,value]) => value > 0).map(([key,value]) => ({ label: this.labels.transform(key), value })));
  readonly subscriptionSeries = computed(() => Object.entries(this.data()?.subscriptions_by_status ?? {}).filter(([,value]) => value > 0).map(([key,value]) => ({ label: this.labels.transform(key), value })));
  readonly mrr = computed(() => this.formatByCurrency(this.data()?.mrr_cents));
  readonly revenue = computed(() => this.formatByCurrency(this.data()?.revenue_30d_cents));

  constructor() {
    this.load();
  }

  load(): void {
    this.error.set(null);
    this.api.overview().subscribe({ next: (d) => this.data.set(d), error: (e) => this.error.set(toApiError(e).message) });
  }

  private formatByCurrency(values: Record<string, number> | undefined): string | null {
    if (!values) return null;
    const entries = Object.entries(values);
    if (!entries.length) return this.money.transform(0);
    return entries.map(([currency, cents]) => this.money.transform(cents, currency)).join(' · ');
  }
}

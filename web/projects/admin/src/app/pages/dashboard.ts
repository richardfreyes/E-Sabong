import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';

import { BarChartComponent, EmptyStateComponent, ErrorStateComponent, PageHeaderComponent, StatCardComponent } from '@shared/components';
import { HasPermissionDirective } from '@shared/directives';
import type { AdminDashboard, TenantProfile } from '@shared/models';
import { LabelPipe, PointsPipe, RelativeTimePipe } from '@shared/pipes';
import { AdminApi, AuthStore, toApiError } from '@shared/services';

@Component({
  selector: 'app-admin-dashboard',
  imports: [PageHeaderComponent, StatCardComponent, BarChartComponent, ErrorStateComponent, EmptyStateComponent, RouterLink, MatButtonModule,
            HasPermissionDirective, LabelPipe, PointsPipe, RelativeTimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header eyebrow="Workspace overview" [title]="'Hello, ' + (auth.user()?.full_name ?? '')" [subtitle]="subtitle()">
      <a *appHasPermission="'events.manage'" mat-flat-button routerLink="/events">Run events</a>
      <a *appHasPermission="'points.transfer'" mat-stroked-button routerLink="/points">Transfer points</a>
    </app-page-header>

    @if (!auth.hasPermission('dashboard.view')) {
      <app-empty-state icon="waving_hand" title="Welcome" message="Your role has no dashboard. Use the navigation to reach the areas you can manage." />
    } @else if (error()) {
      <app-error-state [message]="error()!" (retry)="load()" />
    } @else {
      @let d = data();
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <app-stat-card [label]="d?.points?.scope === 'downline' ? 'Accounts in your network' : 'Accounts'" icon="group" [loading]="!d" [value]="totalUsers()"
                       [hint]="d ? (d.active_users_7d ?? 0) + ' signed in this week' : null" />
        @if (!d || d.events_by_status) {
          <app-stat-card tone="success" label="Live events" icon="sensors" [loading]="!d" [value]="d?.events_by_status?.live ?? null"
                         [hint]="d?.events_by_status ? d.events_by_status.scheduled + ' scheduled' : null" />
          <app-stat-card tone="accent" label="Matches in progress" icon="sports_kabaddi" [loading]="!d" [value]="d?.matches_in_progress ?? null"
                         [hint]="d ? (d.results_7d ?? 0) + ' results declared this week' : null" />
        }
        @if (!d || d.points) {
          <app-stat-card [label]="d?.points?.scope === 'downline' ? 'Your demo points' : 'Demo points in circulation'" icon="toll" [loading]="!d"
                         [value]="(d?.points?.scope === 'downline' ? d?.points?.own_balance : d?.points?.circulating) | points"
                         hint="Non-monetary · not redeemable" />
        }
      </div>

      @if (d?.activity; as activity) {
        <div class="mt-6 grid gap-4 lg:grid-cols-2">
          <section class="arena-panel">
            <p class="arena-eyebrow mb-4">Account activity</p><app-bar-chart title="Sign-ins per day" unit="sign-ins" [data]="series(activity, 'logins')" />
          </section>
          <section class="arena-panel">
            <p class="arena-eyebrow mb-4">Demonstration points</p><app-bar-chart color="secondary" title="Point transactions per day" unit="transactions" [data]="series(activity, 'point_transactions')" />
          </section>
        </div>
      }

      <div class="mt-6 grid gap-4 lg:grid-cols-2">
        @if (d?.users_by_type; as byType) {
          <section class="arena-panel">
            <div class="arena-section-head"><div><h2>Accounts by type</h2><p>Distribution across your workspace</p></div></div>
            <ul class="arena-list">
              @for (t of types; track t) {
                @if (byType[t]) {
                  <li style="display: block"><div class="arena-metric-row"><span>{{ t | label }}</span><strong class="tabular-nums">{{ byType[t] }}</strong></div>
                    <div class="arena-metric-track" aria-hidden="true"><span [style.width.%]="totalUsers() ? (byType[t]! / totalUsers()!) * 100 : 0"></span></div>
                  </li>
                }
              }
            </ul>
          </section>
        }
        @if (d?.recent_audit; as recent) {
          <section class="arena-panel">
            <div class="arena-section-head"><div><h2>Recent activity</h2><p>Latest recorded workspace actions</p></div>
              <a routerLink="/audit" class="text-sm text-primary no-underline">View all</a></div>
            <ul class="arena-list">
              @for (a of recent; track a.id) {
                <li><span class="truncate"><span class="font-medium">{{ a.actor ?? 'system' }}</span> · {{ a.action }}</span>
                  <time [attr.datetime]="a.created_at">{{ a.created_at | relativeTime }}</time></li>
              }
            </ul>
          </section>
        }
      </div>
    }
  `,
})
export class DashboardPage {
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  private readonly api = inject(AdminApi);
  private readonly label = new LabelPipe();
  readonly data = signal<AdminDashboard | null>(null);
  readonly error = signal<string | null>(null);
  readonly types = ['admin', 'operator', 'declarator', 'master_agent', 'agent', 'player'] as const;
  readonly subtitle = computed(() => `${this.label.transform(this.auth.user()?.account_type)} · ${this.auth.user()?.tenant.name ?? ''}`);
  readonly totalUsers = computed(() => {
    const byType = this.data()?.users_by_type;
    return byType ? Object.values(byType).reduce((s, n) => s + (n ?? 0), 0) : null;
  });

  constructor() {
    if (this.auth.hasPermission('dashboard.view')) this.load();
  }

  load(): void {
    this.error.set(null);
    this.api.dashboard().subscribe({ next: (d) => this.data.set(d), error: (e) => this.error.set(toApiError(e).message) });
  }

  series(rows: { date: string; logins: number; point_transactions: number }[], key: 'logins' | 'point_transactions') {
    return rows.map((r) => ({ label: r.date.slice(5), value: r[key] }));
  }
}

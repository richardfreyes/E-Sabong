import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { Observable } from 'rxjs';

import { BarChartComponent, Column, DataTableComponent, PageHeaderComponent, StatCardComponent } from '@shared/components';
import type { DailyActivity, Report } from '@shared/models';
import { LabelPipe } from '@shared/pipes';
import { AdminApi, BrandingService, ToastService, saveBlob, toApiError } from '@shared/services';

type Kind = 'activity' | 'points' | 'matches';
type Row = Record<string, string | number>;

const COLUMNS: Record<Kind, Column<Row>[]> = {
  activity: [
    { key: 'date', label: 'Date' },
    { key: 'logins', label: 'Sign-ins', align: 'end' },
    { key: 'new_accounts', label: 'New accounts', align: 'end' },
    { key: 'point_transactions', label: 'Point transactions', align: 'end', hideOnMobile: true },
    { key: 'points_moved', label: 'Points moved', align: 'end', hideOnMobile: true },
    { key: 'results_declared', label: 'Results declared', align: 'end', hideOnMobile: true },
  ],
  points: [
    { key: 'username', label: 'Account' },
    { key: 'account_type', label: 'Type', hideOnMobile: true },
    { key: 'received', label: 'Received', align: 'end' },
    { key: 'sent', label: 'Sent', align: 'end' },
    { key: 'balance', label: 'Balance now', align: 'end' },
  ],
  matches: [
    { key: 'event', label: 'Event' },
    { key: 'status', label: 'Status', hideOnMobile: true },
    { key: 'matches', label: 'Matches', align: 'end' },
    { key: 'completed', label: 'Completed', align: 'end' },
    { key: 'meron', label: 'Meron', align: 'end', hideOnMobile: true },
    { key: 'wala', label: 'Wala', align: 'end', hideOnMobile: true },
    { key: 'draw', label: 'Draw', align: 'end', hideOnMobile: true },
    { key: 'cancelled', label: 'Cancelled', align: 'end', hideOnMobile: true },
  ],
};

function isoDay(offsetDays: number): string {
  return new Date(Date.now() - offsetDays * 86_400_000).toISOString().slice(0, 10);
}

@Component({
  selector: 'app-admin-reports',
  imports: [ReactiveFormsModule, PageHeaderComponent, DataTableComponent, BarChartComponent, StatCardComponent, MatButtonModule, MatButtonToggleModule,
            MatFormFieldModule, MatInputModule, MatIconModule, LabelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Reports" [subtitle]="report() ? 'Times in ' + report()!.timezone + (report()!.scope === 'downline' ? ' · your downline only' : '') : null">
      @if (csvEnabled()) { <button mat-stroked-button type="button" (click)="download()"><mat-icon>download</mat-icon>Export CSV</button> }
    </app-page-header>
    <div class="mb-4 flex flex-col gap-3 md:flex-row md:items-center">
      <mat-button-toggle-group [value]="kind()" (change)="setKind($event.value)" aria-label="Report" hideSingleSelectionIndicator>
        <mat-button-toggle value="activity">Activity</mat-button-toggle><mat-button-toggle value="points">Points</mat-button-toggle>
        <mat-button-toggle value="matches">Matches</mat-button-toggle>
      </mat-button-toggle-group>
      <form [formGroup]="range" (ngSubmit)="load()" class="flex flex-wrap items-center gap-2">
        <mat-form-field subscriptSizing="dynamic" class="dense w-40"><mat-label>From</mat-label><input matInput type="date" formControlName="from" /></mat-form-field>
        <mat-form-field subscriptSizing="dynamic" class="dense w-40"><mat-label>To</mat-label><input matInput type="date" formControlName="to" /></mat-form-field>
        <button mat-flat-button type="submit">Apply</button>
      </form>
    </div>

    @if (report(); as r) {
      <div class="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        @for (t of totals(); track t.key) { <app-stat-card [label]="t.key | label" [value]="t.value.toLocaleString()" /> }
      </div>
      @if (kind() === 'activity') {
        <div class="mb-4 grid gap-4 lg:grid-cols-2">
          <section class="rounded-2xl border border-outline-variant p-4"><app-bar-chart title="Sign-ins per day" unit="sign-ins" [data]="series('logins')" /></section>
          <section class="rounded-2xl border border-outline-variant p-4"><app-bar-chart title="Results declared per day" unit="results" [data]="series('results_declared')" /></section>
        </div>
      }
    }
    <app-data-table [columns]="columns()" [rows]="report()?.rows ?? []" [total]="report()?.rows?.length ?? 0" [loading]="loading()" [error]="error()"
                    [searchable]="false" [paginated]="false" emptyTitle="No data for this range" emptyIcon="bar_chart" (retry)="load()" />
  `,
})
export class ReportsPage {
  private readonly api = inject(AdminApi);
  private readonly toast = inject(ToastService);
  private readonly branding = inject(BrandingService);
  readonly csvEnabled = computed(() => this.branding.config()?.feature_flags?.['csv_exports'] !== false);
  readonly kind = signal<Kind>('activity');
  readonly report = signal<Report<Row> | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly columns = computed(() => COLUMNS[this.kind()]);
  readonly totals = computed(() => Object.entries(this.report()?.totals ?? {}).map(([key, value]) => ({ key, value })));
  readonly range = inject(FormBuilder).nonNullable.group({ from: isoDay(29), to: isoDay(0) });

  constructor() {
    this.load();
  }

  setKind(k: Kind): void {
    this.kind.set(k);
    this.load();
  }

  load(): void {
    const q = this.range.getRawValue();
    const req: Observable<Report<Row>> =
      this.kind() === 'activity' ? (this.api.activityReport(q) as unknown as Observable<Report<Row>>)
      : this.kind() === 'points' ? this.api.pointsReport(q) : this.api.matchesReport(q);
    this.loading.set(true);
    this.error.set(null);
    req.subscribe({
      next: (r) => (this.report.set(r), this.loading.set(false)),
      error: (e) => (this.error.set(toApiError(e).message), this.loading.set(false)),
    });
  }

  series(key: keyof DailyActivity) {
    return (this.report()?.rows ?? []).map((r) => ({ label: String(r['date']).slice(5), value: Number(r[key]) }));
  }

  download(): void {
    const q = this.range.getRawValue();
    this.api.downloadReport(this.kind(), q).subscribe({
      next: (blob) => saveBlob(blob, `${this.kind()}-${q.from}-${q.to}.csv`),
      error: (e) => this.toast.error(e),
    });
  }
}

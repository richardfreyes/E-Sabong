import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';

import { CellDefDirective, Column, DataTableComponent, PageHeaderComponent, StatCardComponent } from '@shared/components';
import type { PointAccount, PointsSummary } from '@shared/models';
import { PointsPipe } from '@shared/pipes';
import { AdminApi, PagedList, toApiError } from '@shared/services';
import { PointsHistoryComponent } from './points-history';

/** Preserved transferable-points history. No balance changes or transfers are available. */
@Component({
  selector: 'app-admin-points',
  imports: [PointsPipe, MatButtonModule, MatIconModule, PageHeaderComponent, StatCardComponent,
    DataTableComponent, CellDefDirective, PointsHistoryComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './points.html',
  styleUrl: './points.scss',
})
export class PointsPage {
  private readonly api = inject(AdminApi);
  private readonly destroy = inject(DestroyRef);
  readonly summary = signal<PointsSummary | null>(null);
  readonly summaryError = signal<string | null>(null);
  readonly accounts = new PagedList<PointAccount>(q => this.api.pointAccounts(q));
  readonly accountColumns: Column<PointAccount>[] = [
    { key: 'username', label: 'Account' },
    { key: 'full_name', label: 'Name', hideOnMobile: true },
    { key: 'balance', label: 'Archived points', align: 'end' },
    { key: 'account_id', label: 'Legacy account ID', hideOnMobile: true },
  ];

  constructor() {
    this.loadSummary();
    this.accounts.load();
  }

  loadSummary(): void {
    this.api.pointsSummary().pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: summary => { this.summary.set(summary); this.summaryError.set(null); },
      error: error => this.summaryError.set(toApiError(error).message),
    });
  }
}

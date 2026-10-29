import { DatePipe, JsonPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule } from '@angular/material/dialog';

import { CellDefDirective, Column, DataTableComponent, PageHeaderComponent } from '@shared/components';
import type { AuditEntry } from '@shared/models';
import { AdminApi, PagedList } from '@shared/services';

@Component({
  selector: 'app-admin-audit-dialog',
  imports: [MatDialogModule, MatButtonModule, DatePipe, JsonPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ e.action }}</h2>
    <mat-dialog-content>
      <p class="m-0 text-sm">{{ e.created_at | date: 'medium' }} · {{ e.actor_label ?? 'system' }} · {{ e.ip ?? '' }}</p>
      <p class="m-0 mt-1 text-xs text-on-surface-variant">{{ e.resource_type }} {{ e.resource_id }}</p>
      <pre class="mt-3 max-h-80 overflow-auto rounded-lg bg-surface-container p-3 text-xs">{{ e.changes | json }}</pre>
    </mat-dialog-content>
    <mat-dialog-actions align="end"><button mat-button type="button" mat-dialog-close>Close</button></mat-dialog-actions>
  `,
})
export class AdminAuditDialog {
  readonly e = inject<AuditEntry>(MAT_DIALOG_DATA);
}

@Component({
  selector: 'app-admin-audit',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Audit log" subtitle="Every change in this workspace, append-only." />
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                    [loading]="list.loading()" [error]="list.error()" [rowClickable]="true" searchPlaceholder="Search actor, action or id"
                    emptyTitle="No entries" emptyIcon="policy" (search)="list.setSearch($event)" (pageChange)="list.setPage($event)"
                    (retry)="list.load()" (rowClick)="open($event)">
      <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'medium' }}</ng-template>
    </app-data-table>
  `,
})
export class AuditPage {
  private readonly api = inject(AdminApi);
  private readonly dialog = inject(MatDialog);
  readonly list = new PagedList<AuditEntry>((q) => this.api.audit(q));
  readonly columns: Column<AuditEntry>[] = [
    { key: 'created_at', label: 'When' },
    { key: 'action', label: 'Action' },
    { key: 'actor_label', label: 'Actor' },
    { key: 'resource_type', label: 'Resource', hideOnMobile: true },
    { key: 'ip', label: 'IP', hideOnMobile: true },
  ];

  constructor() {
    this.list.load();
  }

  open(e: AuditEntry): void {
    this.dialog.open(AdminAuditDialog, { data: e, width: '560px', maxWidth: '95vw' });
  }
}

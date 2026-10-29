import { DatePipe, JsonPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';

import { CellDefDirective, Column, DataTableComponent, PageHeaderComponent } from '@shared/components';
import type { AuditEntry } from '@shared/models';
import { LabelPipe } from '@shared/pipes';
import { PagedList, PlatformApi } from '@shared/services';

@Component({
  selector: 'app-audit-entry-dialog',
  imports: [MatDialogModule, MatButtonModule, DatePipe, JsonPipe, LabelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ e.action }}</h2>
    <mat-dialog-content>
      <dl class="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt class="text-on-surface-variant">When</dt><dd class="m-0">{{ e.created_at | date: 'medium' }}</dd>
        <dt class="text-on-surface-variant">Actor</dt><dd class="m-0">{{ e.actor_label ?? '—' }} ({{ e.actor_type | label }})</dd>
        <dt class="text-on-surface-variant">Tenant</dt><dd class="m-0">{{ e.tenant_name ?? 'Platform' }}</dd>
        <dt class="text-on-surface-variant">Resource</dt><dd class="m-0">{{ e.resource_type }} {{ e.resource_id }}</dd>
        <dt class="text-on-surface-variant">IP</dt><dd class="m-0">{{ e.ip ?? '—' }}</dd>
        <dt class="text-on-surface-variant">Request</dt><dd class="m-0 font-mono text-xs">{{ e.request_id ?? '—' }}</dd>
      </dl>
      <pre class="mt-3 max-h-80 overflow-auto rounded-lg bg-surface-container p-3 text-xs">{{ e.changes | json }}</pre>
    </mat-dialog-content>
    <mat-dialog-actions align="end"><button mat-button type="button" mat-dialog-close>Close</button></mat-dialog-actions>
  `,
})
export class AuditEntryDialog {
  readonly e = inject<AuditEntry>(MAT_DIALOG_DATA);
}

@Component({
  selector: 'app-owner-audit',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, MatButtonToggleModule, DatePipe, LabelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Audit log" subtitle="Append-only record of platform and tenant actions." />
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                    [loading]="list.loading()" [error]="list.error()" [filters]="true" [rowClickable]="true" searchPlaceholder="Search actor, action or id"
                    emptyTitle="No audit entries" emptyIcon="policy" (search)="list.setSearch($event)" (pageChange)="list.setPage($event)"
                    (retry)="list.load()" (rowClick)="open($event)">
      <mat-button-toggle-group filters [value]="scope()" (change)="setScope($event.value)" aria-label="Scope" hideSingleSelectionIndicator>
        <mat-button-toggle value="all">All</mat-button-toggle><mat-button-toggle value="platform">Platform only</mat-button-toggle>
      </mat-button-toggle-group>
      <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'medium' }}</ng-template>
      <ng-template appCell="actor" let-row>{{ row.actor_label ?? (row.actor_type | label) }}</ng-template>
    </app-data-table>
  `,
})
export class AuditPage {
  private readonly api = inject(PlatformApi);
  private readonly dialog = inject(MatDialog);
  readonly scope = signal('all');
  readonly list = new PagedList<AuditEntry>((q) => this.api.audit(q));
  readonly columns: Column<AuditEntry>[] = [
    { key: 'created_at', label: 'When' },
    { key: 'action', label: 'Action' },
    { key: 'actor', label: 'Actor' },
    { key: 'tenant_name', label: 'Tenant', hideOnMobile: true, cell: (e) => e.tenant_name ?? 'Platform' },
    { key: 'resource_type', label: 'Resource', hideOnMobile: true },
    { key: 'ip', label: 'IP', hideOnMobile: true },
  ];

  constructor() {
    this.list.load();
  }

  setScope(value: string): void {
    this.scope.set(value);
    this.list.setFilter('platform_only', value === 'platform' || null);
  }

  open(e: AuditEntry): void {
    this.dialog.open(AuditEntryDialog, { data: e, width: '560px', maxWidth: '95vw' });
  }
}

import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatDialog, MatDialogModule, MAT_DIALOG_DATA } from '@angular/material/dialog';

import {
  CellDefDirective,
  Column,
  ConfirmService,
  DataTableComponent,
  PageHeaderComponent,
  SecretRevealComponent,
  StatusChipComponent,
} from '@shared/components';
import type { SignupRequest, TenantProvisioned } from '@shared/models';
import { PagedList, PlatformApi, ToastService } from '@shared/services';

@Component({
  selector: 'app-provisioned-dialog',
  imports: [MatDialogModule, MatButtonModule, SecretRevealComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>Tenant provisioned</h2>
    <mat-dialog-content>
      <p class="m-0 mb-3 text-sm">{{ data.tenant.name }} is live at <a [href]="data.login_url" target="_blank" rel="noopener">{{ data.login_url }}</a>.
        The customer administrator was emailed their sign-in details.</p>
      <p class="m-0 mb-2 text-sm">Username: <code>{{ data.admin_username }}</code></p>
      @if (data.temporary_password) { <app-secret-reveal label="Temporary password" [value]="data.temporary_password" /> }
    </mat-dialog-content>
    <mat-dialog-actions align="end"><button mat-flat-button type="button" mat-dialog-close>Done</button></mat-dialog-actions>
  `,
})
export class ProvisionedDialog {
  readonly data = inject<TenantProvisioned>(MAT_DIALOG_DATA);
}

@Component({
  selector: 'app-owner-signups',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, StatusChipComponent, MatButtonModule, MatButtonToggleModule, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Signup requests" subtitle="Approving a request provisions the tenant and emails its administrator." />
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                    [loading]="list.loading()" [error]="list.error()" [filters]="true" searchPlaceholder="Search company or email"
                    emptyTitle="No requests" emptyIcon="how_to_reg" (search)="list.setSearch($event)" (pageChange)="list.setPage($event)" (retry)="list.load()">
      <mat-button-toggle-group filters [value]="status()" (change)="setStatus($event.value)" aria-label="Filter by status" hideSingleSelectionIndicator>
        <mat-button-toggle value="pending">Pending</mat-button-toggle><mat-button-toggle value="approved">Approved</mat-button-toggle>
        <mat-button-toggle value="rejected">Rejected</mat-button-toggle><mat-button-toggle value="">All</mat-button-toggle>
      </mat-button-toggle-group>
      <ng-template appCell="company_name" let-row>
        <div class="font-medium">{{ row.company_name }}</div><div class="text-xs text-on-surface-variant">{{ row.contact_name }} · {{ row.contact_email }}</div>
      </ng-template>
      <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
      <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'medium' }}</ng-template>
      <ng-template appCell="actions" let-row>
        @if (row.status === 'pending') {
          <div class="flex justify-end gap-1">
            <button mat-button type="button" (click)="reject(row)">Reject</button>
            <button mat-flat-button type="button" (click)="approve(row)">Approve</button>
          </div>
        }
      </ng-template>
    </app-data-table>
  `,
})
export class SignupsPage {
  private readonly api = inject(PlatformApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly dialog = inject(MatDialog);
  readonly status = signal('pending');
  readonly list = new PagedList<SignupRequest>((q) => this.api.signups(q), { status: 'pending' });
  readonly columns: Column<SignupRequest>[] = [
    { key: 'company_name', label: 'Company' },
    { key: 'desired_slug', label: 'Slug', hideOnMobile: true },
    { key: 'billing_cycle', label: 'Cycle', hideOnMobile: true },
    { key: 'status', label: 'Status' },
    { key: 'created_at', label: 'Received', hideOnMobile: true },
    { key: 'actions', label: '', align: 'end' },
  ];

  constructor() {
    this.list.load();
  }

  setStatus(value: string): void {
    this.status.set(value);
    this.list.setFilter('status', value || null);
  }

  async approve(row: SignupRequest): Promise<void> {
    const note = await this.confirm.ask({ title: `Approve ${row.company_name}`, message: `Provision ${row.desired_slug} with a free trial.`,
      confirmText: 'Approve and provision', inputLabel: 'Internal note (optional)' });
    if (note === null) return;
    this.api.approveSignup(row.id, { note: note || null, with_trial: true }).subscribe({
      next: (r) => {
        this.dialog.open(ProvisionedDialog, { data: r, width: '520px', maxWidth: '95vw' });
        this.list.load();
      },
      error: (e) => this.toast.error(e),
    });
  }

  async reject(row: SignupRequest): Promise<void> {
    const note = await this.confirm.ask({ title: `Reject ${row.company_name}`, message: 'The request is closed without creating a tenant.',
      confirmText: 'Reject', destructive: true, inputLabel: 'Reason' });
    if (note === null) return;
    this.api.rejectSignup(row.id, note || null).subscribe({ next: () => (this.toast.success('Request rejected'), this.list.load()), error: (e) => this.toast.error(e) });
  }
}

import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import { CellDefDirective, Column, DataTableComponent, FieldErrorComponent, PageHeaderComponent, StatusChipComponent } from '@shared/components';
import type { Deployment, License, Tenant } from '@shared/models';
import { RelativeTimePipe } from '@shared/pipes';
import { PagedList, PlatformApi, ToastService, toApiError } from '@shared/services';

@Component({
  selector: 'app-deployment-dialog',
  imports: [ReactiveFormsModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatSelectModule, MatButtonModule, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ dep ? 'Edit deployment' : 'Register deployment' }}</h2>
    <mat-dialog-content>
      <p class="m-0 mb-3 text-sm text-on-surface-variant">Tracks a dedicated installation provisioned with the deployment runbook. It turns active on its first license heartbeat.</p>
      <form [formGroup]="form" id="dep-form" (ngSubmit)="save()" class="grid grid-cols-1 gap-x-3 sm:grid-cols-2" novalidate>
        <mat-form-field class="sm:col-span-2"><mat-label>Tenant</mat-label>
          <mat-select formControlName="tenant_id" (selectionChange)="loadLicenses($event.value)">
            @for (t of tenants(); track t.id) { <mat-option [value]="t.id">{{ t.name }}</mat-option> }</mat-select>
          <mat-error><app-field-error [control]="form.controls.tenant_id" /></mat-error></mat-form-field>
        <mat-form-field><mat-label>Name</mat-label><input matInput formControlName="name" /><mat-error><app-field-error [control]="form.controls.name" /></mat-error></mat-form-field>
        <mat-form-field><mat-label>Hostname</mat-label><input matInput formControlName="hostname" [readonly]="!!dep" />
          <mat-error><app-field-error [control]="form.controls.hostname" /></mat-error></mat-form-field>
        <mat-form-field><mat-label>Region</mat-label><input matInput formControlName="region" /></mat-form-field>
        <mat-form-field><mat-label>License</mat-label><mat-select formControlName="license_id">
          <mat-option [value]="null">None</mat-option>
          @for (l of licenses(); track l.id) { <mat-option [value]="l.id">{{ l.key_prefix }}… · {{ l.status }}</mat-option> }</mat-select></mat-form-field>
        @if (dep) {
          <mat-form-field><mat-label>Status</mat-label><mat-select formControlName="status">
            @for (s of statuses; track s) { <mat-option [value]="s">{{ s }}</mat-option> }</mat-select></mat-form-field>
        }
        <mat-form-field class="sm:col-span-2"><mat-label>Notes</mat-label><textarea matInput rows="3" formControlName="notes"></textarea></mat-form-field>
        @if (error()) { <p class="m-0 text-sm text-error sm:col-span-2" role="alert">{{ error() }}</p> }
      </form>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close>Cancel</button>
      <button mat-flat-button type="submit" form="dep-form">Save</button>
    </mat-dialog-actions>
  `,
})
export class DeploymentDialog {
  readonly dep = inject<Deployment | null>(MAT_DIALOG_DATA);
  private readonly api = inject(PlatformApi);
  private readonly ref = inject(MatDialogRef<DeploymentDialog>);
  readonly statuses = ['provisioning', 'active', 'suspended', 'decommissioned'];
  readonly tenants = signal<Tenant[]>([]);
  readonly licenses = signal<License[]>([]);
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).group({
    tenant_id: [this.dep?.tenant_id ?? '', Validators.required],
    name: [this.dep?.name ?? '', [Validators.required, Validators.minLength(2)]],
    hostname: [this.dep?.hostname ?? '', [Validators.required, Validators.minLength(4)]],
    region: [this.dep?.region ?? ''],
    license_id: [this.dep?.license_id ?? null as string | null],
    status: [this.dep?.status ?? 'provisioning'],
    notes: [this.dep?.notes ?? ''],
  });

  constructor() {
    this.api.tenants({ page_size: 100 }).subscribe((p) => this.tenants.set(p.items));
    if (this.dep) this.loadLicenses(this.dep.tenant_id);
  }

  loadLicenses(tenantId: string): void {
    this.api.licenses({ tenant_id: tenantId, page_size: 100 }).subscribe((p) => this.licenses.set(p.items));
  }

  save(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    const req = this.dep
      ? this.api.updateDeployment(this.dep.id, { name: v.name, region: v.region || null, license_id: v.license_id, status: v.status, notes: v.notes || null })
      : this.api.createDeployment({ tenant_id: v.tenant_id, name: v.name, hostname: v.hostname, region: v.region || null, license_id: v.license_id, notes: v.notes || null });
    req.subscribe({ next: (d) => this.ref.close(d), error: (e) => this.error.set(toApiError(e).message) });
  }
}

@Component({
  selector: 'app-owner-deployments',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, StatusChipComponent, MatButtonModule, MatIconModule, RelativeTimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Dedicated deployments" subtitle="Licensed single-tenant installations and their last heartbeat.">
      <button mat-flat-button type="button" (click)="edit(null)"><mat-icon>add</mat-icon>Register deployment</button>
    </app-page-header>
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                    [loading]="list.loading()" [error]="list.error()" [rowClickable]="true" searchPlaceholder="Search name or hostname"
                    emptyTitle="No dedicated deployments" emptyIcon="dns" (search)="list.setSearch($event)" (pageChange)="list.setPage($event)"
                    (retry)="list.load()" (rowClick)="edit($event)">
      <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
      <ng-template appCell="last_heartbeat_at" let-row>{{ row.last_heartbeat_at | relativeTime }}</ng-template>
    </app-data-table>
  `,
})
export class DeploymentsPage {
  private readonly api = inject(PlatformApi);
  private readonly dialog = inject(MatDialog);
  private readonly toast = inject(ToastService);
  readonly list = new PagedList<Deployment>((q) => this.api.deployments(q));
  readonly columns: Column<Deployment>[] = [
    { key: 'name', label: 'Name' },
    { key: 'tenant_name', label: 'Tenant', hideOnMobile: true },
    { key: 'hostname', label: 'Hostname' },
    { key: 'app_version', label: 'Version', hideOnMobile: true },
    { key: 'status', label: 'Status' },
    { key: 'last_heartbeat_at', label: 'Last heartbeat', hideOnMobile: true },
  ];

  constructor() {
    this.list.load();
  }

  edit(dep: Deployment | null): void {
    this.dialog.open(DeploymentDialog, { data: dep, width: '600px', maxWidth: '95vw' }).afterClosed().subscribe((d?: Deployment) => {
      if (d) {
        this.toast.success('Deployment saved');
        this.list.load();
      }
    });
  }
}

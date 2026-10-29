import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import {
  CellDefDirective,
  Column,
  ConfirmService,
  DataTableComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  SecretRevealComponent,
  StatusChipComponent,
} from '@shared/components';
import type { License, Tenant } from '@shared/models';
import { PagedList, PlatformApi, ToastService, toApiError } from '@shared/services';

@Component({
  selector: 'app-issue-license-dialog',
  imports: [ReactiveFormsModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatSelectModule, MatButtonModule, FieldErrorComponent, SecretRevealComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ key() ? 'License issued' : 'Issue software license' }}</h2>
    <mat-dialog-content>
      @if (key(); as k) {
        <p class="m-0 mb-3 text-sm">Configure the dedicated installation with <code>LICENSE_KEY</code> and run activation. Only a hash of the key is stored.</p>
        <app-secret-reveal label="License key" [value]="k" />
      } @else {
        <form [formGroup]="form" id="license-form" (ngSubmit)="submit()" class="grid grid-cols-1 gap-x-3 sm:grid-cols-2" novalidate>
          <mat-form-field class="sm:col-span-2"><mat-label>Tenant</mat-label>
            <mat-select formControlName="tenant_id">@for (t of tenants(); track t.id) { <mat-option [value]="t.id">{{ t.name }} ({{ t.deployment_mode }})</mat-option> }</mat-select>
            <mat-error><app-field-error [control]="form.controls.tenant_id" /></mat-error></mat-form-field>
          <mat-form-field class="sm:col-span-2"><mat-label>Licensee (legal name)</mat-label><input matInput formControlName="licensee_name" />
            <mat-error><app-field-error [control]="form.controls.licensee_name" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>License type</mat-label><mat-select formControlName="license_type">
            <mat-option value="perpetual">Perpetual</mat-option><mat-option value="term">Term</mat-option></mat-select></mat-form-field>
          <mat-form-field><mat-label>Max activations</mat-label><input matInput type="number" min="1" formControlName="max_activations" /></mat-form-field>
          @if (form.controls.license_type.value === 'term') {
            <mat-form-field><mat-label>Expires on</mat-label><input matInput type="date" formControlName="expires_at" /></mat-form-field>
          }
          <mat-form-field><mat-label>Maintenance until</mat-label><input matInput type="date" formControlName="maintenance_until" /></mat-form-field>
          <mat-form-field class="sm:col-span-2"><mat-label>Agreement reference</mat-label><input matInput formControlName="agreement_reference" /></mat-form-field>
          @if (error()) { <p class="m-0 text-sm text-error sm:col-span-2" role="alert">{{ error() }}</p> }
        </form>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      @if (key()) { <button mat-flat-button type="button" [mat-dialog-close]="true">Done</button> }
      @else {
        <button mat-button type="button" mat-dialog-close>Cancel</button>
        <button mat-flat-button type="submit" form="license-form">Issue license</button>
      }
    </mat-dialog-actions>
  `,
})
export class IssueLicenseDialog {
  private readonly api = inject(PlatformApi);
  readonly ref = inject(MatDialogRef<IssueLicenseDialog>);
  readonly tenants = signal<Tenant[]>([]);
  readonly key = signal<string | null>(null);
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).group({
    tenant_id: ['', Validators.required],
    licensee_name: ['', [Validators.required, Validators.minLength(2)]],
    license_type: ['perpetual'],
    max_activations: [1, [Validators.min(1)]],
    expires_at: [''],
    maintenance_until: [''],
    agreement_reference: [''],
  });

  constructor() {
    this.api.tenants({ page_size: 100 }).subscribe((p) => this.tenants.set(p.items));
  }

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    const iso = (d: string | null) => (d ? new Date(`${d}T23:59:59Z`).toISOString() : null);
    this.api.issueLicense({
      tenant_id: v.tenant_id, licensee_name: v.licensee_name, license_type: v.license_type, max_activations: Number(v.max_activations),
      expires_at: v.license_type === 'term' ? iso(v.expires_at) : null, maintenance_until: iso(v.maintenance_until),
      agreement_reference: v.agreement_reference || null,
    }).subscribe({ next: (r) => this.key.set(r.license_key), error: (e) => this.error.set(toApiError(e).message) });
  }
}

@Component({
  selector: 'app-owner-licenses',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, StatusChipComponent, MatButtonModule, MatIconModule, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Software licenses" subtitle="Ed25519-signed licenses for dedicated installations; verified offline, re-checked by heartbeat.">
      <button mat-flat-button type="button" (click)="issue()"><mat-icon>key</mat-icon>Issue license</button>
    </app-page-header>
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                    [loading]="list.loading()" [error]="list.error()" searchPlaceholder="Search licensee or key prefix" emptyTitle="No licenses" emptyIcon="key"
                    (search)="list.setSearch($event)" (pageChange)="list.setPage($event)" (retry)="list.load()">
      <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
      <ng-template appCell="key_prefix" let-row><code>{{ row.key_prefix }}…</code></ng-template>
      <ng-template appCell="term" let-row>
        {{ row.license_type === 'perpetual' ? 'Perpetual' : 'Until ' + (row.expires_at | date: 'mediumDate') }}
        @if (row.maintenance_until) { <div class="text-xs text-on-surface-variant">Maintenance to {{ row.maintenance_until | date: 'mediumDate' }}</div> }
      </ng-template>
      <ng-template appCell="actions" let-row>
        @if (row.status !== 'revoked') { <button mat-button type="button" class="!text-error" (click)="revoke(row)">Revoke</button> }
      </ng-template>
    </app-data-table>
  `,
})
export class LicensesPage {
  private readonly api = inject(PlatformApi);
  private readonly dialog = inject(MatDialog);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  readonly list = new PagedList<License>((q) => this.api.licenses(q));
  readonly columns: Column<License>[] = [
    { key: 'licensee_name', label: 'Licensee' },
    { key: 'tenant_name', label: 'Tenant', hideOnMobile: true },
    { key: 'key_prefix', label: 'Key', hideOnMobile: true },
    { key: 'term', label: 'Term' },
    { key: 'activations', label: 'Activations', align: 'end', hideOnMobile: true, cell: (l) => `${l.activation_count} / ${l.max_activations}` },
    { key: 'status', label: 'Status' },
    { key: 'actions', label: '', align: 'end' },
  ];

  constructor() {
    this.list.load();
  }

  issue(): void {
    this.dialog.open(IssueLicenseDialog, { width: '600px', maxWidth: '95vw', disableClose: true }).afterClosed().subscribe((done) => done && this.list.load());
  }

  async revoke(l: License): Promise<void> {
    const reason = await this.confirm.ask({ title: `Revoke license for ${l.licensee_name}`, message: 'The installation stops serving traffic at its next heartbeat.',
      confirmText: 'Revoke', destructive: true, inputLabel: 'Reason', inputRequired: true, inputMinLength: 3 });
    if (reason === null) return;
    this.api.revokeLicense(l.id, reason).subscribe({ next: () => (this.toast.success('License revoked'), this.list.load()), error: (e) => this.toast.error(e) });
  }
}

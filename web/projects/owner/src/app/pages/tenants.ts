import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { Router } from '@angular/router';

import {
  CellDefDirective,
  Column,
  DataTableComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  SecretRevealComponent,
  StatusChipComponent,
} from '@shared/components';
import type { Plan, Tenant, TenantProvisioned } from '@shared/models';
import { PagedList, PlatformApi, ToastService, toApiError } from '@shared/services';

@Component({
  selector: 'app-create-tenant-dialog',
  imports: [ReactiveFormsModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatSelectModule, MatCheckboxModule, MatButtonModule,
            FieldErrorComponent, SecretRevealComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ result() ? 'Tenant provisioned' : 'New tenant' }}</h2>
    <mat-dialog-content>
      @if (result(); as r) {
        <p class="m-0 mb-3 text-sm">Workspace <strong>{{ r.tenant.name }}</strong> is live at <a [href]="r.login_url" target="_blank" rel="noopener">{{ r.login_url }}</a>.
          The administrator was emailed these credentials.</p>
        <p class="m-0 mb-2 text-sm">Username: <code>{{ r.admin_username }}</code></p>
        @if (r.temporary_password) {
          <app-secret-reveal label="Temporary password (must be changed at first sign-in)" [value]="r.temporary_password" />
        }
      } @else {
        <form [formGroup]="form" id="tenant-form" (ngSubmit)="submit()" class="grid grid-cols-1 gap-x-3 sm:grid-cols-2" novalidate>
          <mat-form-field class="sm:col-span-2"><mat-label>Workspace name</mat-label><input matInput formControlName="name" (input)="suggestSlug()" />
            <mat-error><app-field-error [control]="form.controls.name" /></mat-error></mat-form-field>
          <mat-form-field class="sm:col-span-2"><mat-label>Address (slug)</mat-label><input matInput formControlName="slug" />
            <mat-hint>{{ form.controls.slug.value || 'slug' }}.your-platform-domain</mat-hint>
            <mat-error><app-field-error [control]="form.controls.slug" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Deployment</mat-label>
            <mat-select formControlName="deployment_mode"><mat-option value="shared">Shared SaaS</mat-option><mat-option value="dedicated">Dedicated (licensed)</mat-option></mat-select></mat-form-field>
          <mat-form-field><mat-label>Plan</mat-label>
            <mat-select formControlName="plan_id">
              <mat-option [value]="null">No plan (licensed install)</mat-option>
              @for (p of plans(); track p.id) { <mat-option [value]="p.id">{{ p.name }}</mat-option> }
            </mat-select></mat-form-field>
          <mat-form-field><mat-label>Billing cycle</mat-label>
            <mat-select formControlName="billing_cycle"><mat-option value="monthly">Monthly</mat-option><mat-option value="annual">Annual</mat-option></mat-select></mat-form-field>
          <mat-checkbox formControlName="with_trial" class="self-center">Start with free trial</mat-checkbox>
          <mat-form-field><mat-label>Administrator name</mat-label><input matInput formControlName="admin_name" />
            <mat-error><app-field-error [control]="form.controls.admin_name" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Administrator email</mat-label><input matInput type="email" formControlName="admin_email" />
            <mat-error><app-field-error [control]="form.controls.admin_email" /></mat-error></mat-form-field>
          @if (error()) { <p class="m-0 text-sm text-error sm:col-span-2" role="alert">{{ error() }}</p> }
        </form>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      @if (result()) {
        <button mat-flat-button type="button" [mat-dialog-close]="result()">Done</button>
      } @else {
        <button mat-button type="button" mat-dialog-close>Cancel</button>
        <button mat-flat-button type="submit" form="tenant-form" [disabled]="busy()" [attr.aria-busy]="busy()">{{ busy() ? 'Provisioning…' : 'Create tenant' }}</button>
      }
    </mat-dialog-actions>
  `,
})
export class CreateTenantDialog {
  private readonly api = inject(PlatformApi);
  readonly plans = signal<Plan[]>(inject<{ plans: Plan[] }>(MAT_DIALOG_DATA).plans);
  readonly ref = inject(MatDialogRef<CreateTenantDialog>);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly result = signal<TenantProvisioned | null>(null);
  readonly form = inject(FormBuilder).group({
    name: ['', [Validators.required, Validators.minLength(2)]],
    slug: ['', [Validators.required, Validators.pattern(/^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$/)]],
    deployment_mode: ['shared'],
    plan_id: [this.plans()[0]?.id ?? null as string | null],
    billing_cycle: ['monthly'],
    with_trial: [true],
    admin_name: ['', Validators.required],
    admin_email: ['', [Validators.required, Validators.email]],
  });

  suggestSlug(): void {
    const slug = this.form.controls.slug;
    if (slug.dirty) return;
    slug.setValue((this.form.controls.name.value ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 63));
  }

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.api.createTenant(this.form.getRawValue()).subscribe({
      next: (r) => {
        this.busy.set(false);
        this.result.set(r);
      },
      error: (e) => {
        this.busy.set(false);
        this.error.set(toApiError(e).message);
      },
    });
  }
}

@Component({
  selector: 'app-owner-tenants',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, StatusChipComponent, MatButtonModule, MatIconModule, MatButtonToggleModule, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Tenants" subtitle="Every customer workspace, shared or dedicated.">
      <button mat-flat-button type="button" (click)="create()"><mat-icon>add</mat-icon>New tenant</button>
    </app-page-header>
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                    [loading]="list.loading()" [error]="list.error()" [filters]="true" [rowClickable]="true"
                    searchPlaceholder="Search name or slug" emptyTitle="No tenants match" emptyIcon="domain"
                    (search)="list.setSearch($event)" (sortChange)="list.setSort($event)" (pageChange)="list.setPage($event)"
                    (retry)="list.load()" (rowClick)="open($event)">
      <mat-button-toggle-group filters [value]="status()" (change)="setStatus($event.value)" aria-label="Filter by status" hideSingleSelectionIndicator>
        <mat-button-toggle value="">All</mat-button-toggle>
        <mat-button-toggle value="active">Active</mat-button-toggle>
        <mat-button-toggle value="suspended">Suspended</mat-button-toggle>
        <mat-button-toggle value="cancelled">Cancelled</mat-button-toggle>
      </mat-button-toggle-group>
      <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
      <ng-template appCell="subscription_status" let-row>
        @if (row.subscription_status) { <app-status-chip [status]="row.subscription_status" [text]="row.plan_name + ' · ' + row.subscription_status" /> }
        @else { <span class="text-on-surface-variant">{{ row.deployment_mode === 'dedicated' ? 'Licensed' : 'None' }}</span> }
      </ng-template>
      <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'mediumDate' }}</ng-template>
    </app-data-table>
  `,
})
export class TenantsPage {
  private readonly api = inject(PlatformApi);
  private readonly dialog = inject(MatDialog);
  private readonly router = inject(Router);
  private readonly toast = inject(ToastService);
  readonly list = new PagedList<Tenant>((q) => this.api.tenants(q));
  readonly status = signal('');
  readonly columns: Column<Tenant>[] = [
    { key: 'name', label: 'Name', sortable: true },
    { key: 'primary_domain', label: 'Domain', hideOnMobile: true },
    { key: 'status', label: 'Status' },
    { key: 'subscription_status', label: 'Plan', hideOnMobile: true },
    { key: 'user_count', label: 'Accounts', align: 'end', hideOnMobile: true },
    { key: 'created_at', label: 'Created', sortable: true, hideOnMobile: true },
  ];

  constructor() {
    this.list.load();
  }

  setStatus(value: string): void {
    this.status.set(value);
    this.list.setFilter('status', value || null);
  }

  open(t: Tenant): void {
    void this.router.navigate(['/tenants', t.id]);
  }

  create(): void {
    this.api.plans().subscribe({
      next: (plans) => {
        const saas = plans.filter((p) => p.kind === 'saas' && p.is_active);
        this.dialog
          .open(CreateTenantDialog, { data: { plans: saas }, width: '640px', maxWidth: '95vw', disableClose: true })
          .afterClosed()
          .subscribe((r?: TenantProvisioned) => {
            if (r) {
              this.toast.success(`${r.tenant.name} provisioned`);
              this.list.load();
            }
          });
      },
      error: (e) => this.toast.error(e),
    });
  }
}

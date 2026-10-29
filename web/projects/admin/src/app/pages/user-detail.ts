import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog, MatDialogModule, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { Router, RouterLink } from '@angular/router';

import {
  CellDefDirective,
  Column,
  ConfirmService,
  DataTableComponent,
  ErrorStateComponent,
  FieldErrorComponent,
  LoadingComponent,
  PageHeaderComponent,
  SecretRevealComponent,
  StatusChipComponent,
} from '@shared/components';
import { HasPermissionDirective } from '@shared/directives';
import type { LedgerEntry, Role, TenantProfile, User } from '@shared/models';
import { LabelPipe, PointsPipe, RelativeTimePipe } from '@shared/pipes';
import { AdminApi, AuthStore, BrandingService, PagedList, ToastService, toApiError } from '@shared/services';
import { PARENT_TYPES } from './user-form';

@Component({
  selector: 'app-temp-password-dialog',
  imports: [MatDialogModule, MatButtonModule, SecretRevealComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>Password reset</h2>
    <mat-dialog-content>
      <p class="m-0 mb-3 text-sm">All sessions of this account were signed out. Share the temporary password securely.</p>
      <app-secret-reveal label="Temporary password" [value]="password" />
    </mat-dialog-content>
    <mat-dialog-actions align="end"><button mat-flat-button type="button" mat-dialog-close>Done</button></mat-dialog-actions>
  `,
})
export class TempPasswordDialog {
  readonly password = inject<string>(MAT_DIALOG_DATA);
}

@Component({
  selector: 'app-admin-user-detail',
  imports: [
    RouterLink, ReactiveFormsModule, DatePipe, LabelPipe, PointsPipe, RelativeTimePipe, MatButtonModule, MatIconModule, MatFormFieldModule,
    MatInputModule, MatSelectModule, PageHeaderComponent, StatusChipComponent, LoadingComponent, ErrorStateComponent, FieldErrorComponent,
    DataTableComponent, CellDefDirective, HasPermissionDirective,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (error()) {
      <app-error-state [message]="error()!" (retry)="load()" />
    } @else if (!user()) {
      <app-loading />
    } @else {
      @let u = user()!;
      <app-page-header [title]="u.full_name" [subtitle]="'@' + u.username + ' · ' + (u.account_type | label)">
        <a breadcrumb routerLink="/users" class="mb-1 inline-flex items-center gap-1 text-sm text-primary no-underline"><mat-icon class="!h-4 !w-4 !text-base">arrow_back</mat-icon>Accounts</a>
        <app-status-chip [status]="u.status" />
        @if (canManage() && !isSelf()) {
          @if (u.status === 'active') { <button mat-stroked-button type="button" (click)="suspend()"><mat-icon>block</mat-icon>Suspend</button> }
          @else { <button mat-flat-button type="button" (click)="activate()"><mat-icon>check</mat-icon>Activate</button> }
          <button mat-stroked-button type="button" (click)="resetPassword()"><mat-icon>key</mat-icon>Reset password</button>
          <button *appHasPermission="'users.manage'" mat-button type="button" class="!text-error" (click)="remove()"><mat-icon>delete</mat-icon>Delete</button>
        }
      </app-page-header>

      <div class="grid gap-6 lg:grid-cols-3">
        <section class="rounded-2xl border border-outline-variant p-4 lg:col-span-2">
          <h2 class="m-0 mb-3 text-base font-semibold">Details</h2>
          <form [formGroup]="form" (ngSubmit)="save()" class="grid grid-cols-1 gap-x-3 sm:grid-cols-2" novalidate>
            <mat-form-field><mat-label>Full name</mat-label><input matInput formControlName="full_name" [readonly]="!canManage()" />
              <mat-error><app-field-error [control]="form.controls.full_name" /></mat-error></mat-form-field>
            <mat-form-field><mat-label>Email</mat-label><input matInput formControlName="email" [readonly]="!canManage()" />
              <mat-error><app-field-error [control]="form.controls.email" /></mat-error></mat-form-field>
            <mat-form-field><mat-label>Phone</mat-label><input matInput formControlName="phone" [readonly]="!canManage()" /></mat-form-field>
            @if (parentTypes().length) {
              <mat-form-field><mat-label>Parent account</mat-label>
                <mat-select formControlName="parent_id" [disabled]="!canManage()">
                  @if (u.account_type === 'player') { <mat-option [value]="null">None</mat-option> }
                  @for (p of parents(); track p.id) { <mat-option [value]="p.id">{{ p.username }} · {{ p.account_type | label }}</mat-option> }
                </mat-select></mat-form-field>
            }
            @if (auth.hasPermission('users.manage')) {
              <mat-form-field class="sm:col-span-2"><mat-label>Roles</mat-label>
                <mat-select formControlName="role_ids" multiple>@for (r of roles(); track r.id) { <mat-option [value]="r.id">{{ r.name | label }}</mat-option> }</mat-select></mat-form-field>
            }
            @if (canManage()) { <div class="sm:col-span-2"><button mat-flat-button type="submit" [disabled]="form.pristine">Save changes</button></div> }
          </form>
        </section>
        <section class="flex flex-col gap-3 rounded-2xl border border-outline-variant p-4">
          <h2 class="m-0 text-base font-semibold">Summary</h2>
          <dl class="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
            <dt class="text-on-surface-variant">Parent</dt><dd class="m-0">{{ u.parent_username ?? 'Top level' }}</dd>
            <dt class="text-on-surface-variant">Roles</dt><dd class="m-0">{{ roleNames() }}</dd>
            <dt class="text-on-surface-variant">Created</dt><dd class="m-0">{{ u.created_at | date: 'mediumDate' }}</dd>
            <dt class="text-on-surface-variant">Last sign-in</dt><dd class="m-0">{{ u.last_login_at | relativeTime }}</dd>
            @if (u.must_change_password) { <dt class="text-on-surface-variant">Password</dt><dd class="m-0">Temporary, not yet changed</dd> }
          </dl>
          @if (branding.hasModule('points')) {
            <div class="rounded-xl bg-surface-container p-3">
              <p class="m-0 text-xs text-on-surface-variant">Demonstration points</p>
              <p class="m-0 text-2xl font-semibold tabular-nums">{{ u.balance | points }}</p>
              <a routerLink="/points" [queryParams]="{ user: u.id }" class="text-sm text-primary no-underline">Transfer or allocate</a>
            </div>
          }
          @if (u.account_type === 'master_agent' || u.account_type === 'agent') {
            <a routerLink="/hierarchy" [queryParams]="{ root: u.id }" class="text-sm text-primary no-underline">View downline</a>
          }
        </section>
      </div>

      @if (branding.hasModule('points')) {
        <section class="mt-6">
          <h2 class="m-0 mb-3 text-base font-semibold">Points statement</h2>
          <app-data-table [columns]="entryColumns" [rows]="entries.rows()" [total]="entries.total()" [page]="entries.page()" [pageSize]="entries.pageSize()"
                          [loading]="entries.loading()" [error]="entries.error()" [searchable]="false" emptyTitle="No point movements" emptyIcon="toll"
                          (pageChange)="entries.setPage($event)" (retry)="entries.load()">
            <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'medium' }}</ng-template>
            <ng-template appCell="delta" let-row><span [class.text-error]="row.delta < 0">{{ row.delta | points: true }}</span></ng-template>
            <ng-template appCell="balance_after" let-row>{{ row.balance_after | points }}</ng-template>
            <ng-template appCell="kind" let-row>{{ row.kind | label }}</ng-template>
          </app-data-table>
        </section>
      }
    }
  `,
})
export class UserDetailPage {
  readonly id = input.required<string>();
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly branding = inject(BrandingService);
  private readonly api = inject(AdminApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly dialog = inject(MatDialog);
  private readonly router = inject(Router);

  readonly user = signal<User | null>(null);
  readonly error = signal<string | null>(null);
  readonly roles = signal<Role[]>([]);
  readonly candidates = signal<User[]>([]);
  readonly canManage = computed(() => this.auth.hasAnyPermission('users.manage', 'users.manage_downline'));
  readonly isSelf = computed(() => this.user()?.id === this.auth.user()?.id);
  readonly roleNames = computed(() => this.user()?.roles.map((r) => r.name).join(', ') || '—');
  readonly parentTypes = computed(() => (this.user() ? PARENT_TYPES[this.user()!.account_type] : []));
  readonly parents = computed(() => this.candidates().filter((c) => this.parentTypes().includes(c.account_type) && c.id !== this.user()?.id));
  readonly entries = new PagedList<LedgerEntry>((q) => this.api.accountEntries(this.id(), q));
  readonly entryColumns: Column<LedgerEntry>[] = [
    { key: 'created_at', label: 'When' },
    { key: 'kind', label: 'Type' },
    { key: 'counterparty', label: 'Counterparty', hideOnMobile: true, cell: (e) => e.counterparty?.username ?? e.counterparty?.full_name ?? '—' },
    { key: 'memo', label: 'Memo', hideOnMobile: true },
    { key: 'delta', label: 'Change', align: 'end' },
    { key: 'balance_after', label: 'Balance', align: 'end' },
  ];
  readonly form = inject(FormBuilder).group({
    full_name: ['', [Validators.required, Validators.maxLength(200)]],
    email: ['', Validators.email],
    phone: [''],
    parent_id: [null as string | null],
    role_ids: [[] as string[]],
  });

  constructor() {
    effect(() => {
      this.id();
      this.load();
      if (this.branding.hasModule('points')) this.entries.load();
    });
    if (this.auth.hasPermission('users.manage')) this.api.roles().subscribe((r) => this.roles.set(r));
    this.api.users({ page_size: 100 }).subscribe((p) => this.candidates.set(p.items.filter((u) => u.account_type === 'master_agent' || u.account_type === 'agent')));
  }

  load(): void {
    this.error.set(null);
    this.api.user(this.id()).subscribe({
      next: (u) => {
        this.user.set(u);
        this.form.reset({ full_name: u.full_name, email: u.email ?? '', phone: u.phone ?? '', parent_id: u.parent_id, role_ids: u.roles.map((r) => r.id) });
      },
      error: (e) => this.error.set(toApiError(e).message),
    });
  }

  save(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    const body: Record<string, unknown> = { full_name: v.full_name, email: v.email || null, phone: v.phone || null };
    if (this.form.controls.parent_id.dirty) body['parent_id'] = v.parent_id;
    if (this.form.controls.role_ids.dirty) body['role_ids'] = v.role_ids;
    this.api.updateUser(this.id(), body).subscribe({ next: (u) => (this.toast.success('Account updated'), this.user.set(u), this.form.markAsPristine()), error: (e) => this.toast.error(e) });
  }

  async suspend(): Promise<void> {
    if ((await this.confirm.ask({ title: 'Suspend account', message: 'The account is signed out everywhere and cannot sign in until reactivated.', confirmText: 'Suspend', destructive: true })) === null) return;
    this.api.suspendUser(this.id()).subscribe({ next: (u) => this.user.set(u), error: (e) => this.toast.error(e) });
  }

  activate(): void {
    this.api.activateUser(this.id()).subscribe({ next: (u) => this.user.set(u), error: (e) => this.toast.error(e) });
  }

  async resetPassword(): Promise<void> {
    if ((await this.confirm.ask({ title: 'Reset password', message: 'A temporary password is generated and every session of this account is ended.', confirmText: 'Reset password' })) === null) return;
    this.api.resetPassword(this.id()).subscribe({
      next: (r) => (this.dialog.open(TempPasswordDialog, { data: r.temporary_password, width: '480px', maxWidth: '95vw' }), this.load()),
      error: (e) => this.toast.error(e),
    });
  }

  async remove(): Promise<void> {
    if ((await this.confirm.ask({ title: 'Delete account', message: 'The account is deactivated and hidden. Ledger and audit history are kept.', confirmText: 'Delete', destructive: true })) === null) return;
    this.api.deleteUser(this.id()).subscribe({ next: () => (this.toast.success('Account deleted'), void this.router.navigate(['/users'])), error: (e) => this.toast.error(e) });
  }
}

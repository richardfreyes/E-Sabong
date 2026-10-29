import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';

import { FieldErrorComponent, SecretRevealComponent } from '@shared/components';
import type { AccountType, Role, TenantProfile, User, UserCreated } from '@shared/models';
import { LabelPipe } from '@shared/pipes';
import { AdminApi, AuthStore, assignableRoles, toApiError } from '@shared/services';

// Which parent types each account type accepts (mirrors the API hierarchy rules).
export const PARENT_TYPES: Record<AccountType, AccountType[]> = {
  admin: [], operator: [], declarator: [], master_agent: [], agent: ['master_agent'], player: ['agent', 'master_agent'],
};

@Component({
  selector: 'app-create-user-dialog',
  imports: [ReactiveFormsModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatSelectModule, MatSlideToggleModule, MatButtonModule,
            FieldErrorComponent, SecretRevealComponent, LabelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ created() ? 'Account created' : 'New account' }}</h2>
    <mat-dialog-content>
      @if (created(); as c) {
        <p class="m-0 mb-3 text-sm"><strong>{{ c.user.username }}</strong> was created as {{ c.user.account_type | label }}.</p>
        @if (c.temporary_password) {
          <app-secret-reveal label="Temporary password — share it securely; it must be changed at first sign-in" [value]="c.temporary_password" />
        }
      } @else {
        <form [formGroup]="form" id="user-form" (ngSubmit)="submit()" class="grid grid-cols-1 gap-x-3 sm:grid-cols-2" novalidate>
          <mat-form-field><mat-label>Account type</mat-label>
            <mat-select formControlName="account_type" (selectionChange)="onTypeChange()">
              @for (t of allowedTypes(); track t) { <mat-option [value]="t">{{ t | label }}</mat-option> }
            </mat-select></mat-form-field>
          @if (parentOptions().length || needsParent()) {
            <mat-form-field><mat-label>Parent account</mat-label>
              <mat-select formControlName="parent_id">
                @if (!needsParent()) { <mat-option [value]="null">None (top level)</mat-option> }
                @for (p of parentOptions(); track p.id) { <mat-option [value]="p.id">{{ p.username }} · {{ p.account_type | label }}</mat-option> }
              </mat-select>
              <mat-error>A parent is required</mat-error></mat-form-field>
          }
          <mat-form-field><mat-label>Username</mat-label><input matInput formControlName="username" autocomplete="off" />
            <mat-error><app-field-error [control]="form.controls.username" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Full name</mat-label><input matInput formControlName="full_name" />
            <mat-error><app-field-error [control]="form.controls.full_name" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Email (optional)</mat-label><input matInput type="email" formControlName="email" />
            <mat-error><app-field-error [control]="form.controls.email" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Phone (optional)</mat-label><input matInput formControlName="phone" /></mat-form-field>
          @if (canAssignRoles()) {
            <mat-form-field class="sm:col-span-2"><mat-label>Roles</mat-label>
              <mat-select formControlName="role_ids" multiple>@for (r of rolesForType(); track r.id) { <mat-option [value]="r.id">{{ r.name | label }}</mat-option> }</mat-select>
              <mat-hint>Leave empty for the default role of the account type</mat-hint></mat-form-field>
          }
          <mat-slide-toggle formControlName="set_password" class="sm:col-span-2">Set a password now (otherwise a temporary one is generated)</mat-slide-toggle>
          @if (form.controls.set_password.value) {
            <mat-form-field class="sm:col-span-2 mt-2"><mat-label>Password</mat-label><input matInput type="password" formControlName="password" autocomplete="new-password" />
              <mat-error><app-field-error [control]="form.controls.password" /></mat-error></mat-form-field>
          }
          @if (error()) { <p class="m-0 text-sm text-error sm:col-span-2" role="alert">{{ error() }}</p> }
        </form>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      @if (created()) { <button mat-flat-button type="button" [mat-dialog-close]="created()">Done</button> }
      @else {
        <button mat-button type="button" mat-dialog-close>Cancel</button>
        <button mat-flat-button type="submit" form="user-form" [disabled]="busy()" [attr.aria-busy]="busy()">Create account</button>
      }
    </mat-dialog-actions>
  `,
})
export class CreateUserDialog {
  private readonly api = inject(AdminApi);
  private readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  private readonly data = inject<{ type?: AccountType; parentId?: string } | null>(MAT_DIALOG_DATA, { optional: true });
  readonly roles = signal<Role[]>([]);
  readonly candidates = signal<User[]>([]);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly created = signal<UserCreated | null>(null);
  readonly canAssignRoles = computed(() => this.auth.hasPermission('users.manage'));
  readonly allowedTypes = computed<AccountType[]>(() => {
    if (this.auth.hasPermission('users.manage')) return ['player', 'agent', 'master_agent', 'operator', 'declarator', 'admin'];
    return this.auth.user()?.account_type === 'master_agent' ? ['player', 'agent'] : ['player'];
  });

  readonly form = inject(FormBuilder).group({
    account_type: [this.data?.type ?? 'player' as AccountType, Validators.required],
    parent_id: [this.data?.parentId ?? null as string | null],
    username: ['', [Validators.required, Validators.minLength(3), Validators.maxLength(64), Validators.pattern(/^[A-Za-z0-9_.-]+$/)]],
    full_name: ['', [Validators.required, Validators.maxLength(200)]],
    email: ['', Validators.email],
    phone: [''],
    role_ids: [[] as string[]],
    set_password: [false],
    password: [''],
  });
  private readonly type = signal<AccountType>(this.form.controls.account_type.value!);
  readonly needsParent = computed(() => this.type() === 'agent');
  readonly rolesForType = computed(() => assignableRoles(this.roles(), this.type()));
  readonly parentOptions = computed(() => {
    const allowed = PARENT_TYPES[this.type()];
    const self = this.auth.user();
    const pool = [...this.candidates()];
    if (self && !this.auth.hasPermission('users.manage') && allowed.includes(self.account_type)) {
      pool.unshift({ id: self.id, username: `${self.username} (you)`, account_type: self.account_type } as User);
    }
    return pool.filter((u) => allowed.includes(u.account_type));
  });

  constructor() {
    if (this.canAssignRoles()) this.api.roles().subscribe((r) => this.roles.set(r));
    this.api.users({ page_size: 100, status: 'active' }).subscribe((p) => this.candidates.set(p.items.filter((u) => u.account_type === 'master_agent' || u.account_type === 'agent')));
  }

  onTypeChange(): void {
    this.type.set(this.form.controls.account_type.value!);
    this.form.controls.parent_id.setValue(null);
    const allowed = new Set(this.rolesForType().map((r) => r.id));
    this.form.controls.role_ids.setValue((this.form.controls.role_ids.value ?? []).filter((id) => allowed.has(id)));
  }

  submit(): void {
    const v = this.form.getRawValue();
    if (this.needsParent() && !v.parent_id) this.form.controls.parent_id.setErrors({ required: true });
    if (v.set_password && (v.password ?? '').length < 10) this.form.controls.password.setErrors({ minlength: { requiredLength: 10 } });
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.api.createUser({
      account_type: v.account_type, parent_id: v.parent_id, username: v.username, full_name: v.full_name,
      email: v.email || null, phone: v.phone || null, role_ids: v.role_ids?.length ? v.role_ids : null,
      password: v.set_password ? v.password : null,
    }).subscribe({
      next: (c) => (this.busy.set(false), this.created.set(c)),
      error: (e) => (this.busy.set(false), this.error.set(toApiError(e).message)),
    });
  }
}

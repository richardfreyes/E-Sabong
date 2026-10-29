import { ChangeDetectionStrategy, Component, effect, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import type { TenantProfile } from '@shared/models';
import { LabelPipe } from '../pipes';
import { AccountApi } from '../services/tenant-api';
import { AuthStore } from '../services/auth.store';
import { ToastService } from '../services/toast.service';
import { PasswordFormComponent } from './password-form';
import { FieldErrorComponent, PageHeaderComponent } from './primitives';

/** Profile and password management for tenant users (admin and player portals). */
@Component({
  selector: 'app-tenant-profile',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule, PageHeaderComponent, PasswordFormComponent, FieldErrorComponent, LabelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Profile & security" [subtitle]="'@' + (auth.user()?.username ?? '') + ' · ' + (auth.user()?.account_type | label)" />
    <div class="grid gap-6 lg:grid-cols-2">
      <section class="rounded-2xl border border-outline-variant p-4">
        <h2 class="m-0 mb-3 text-base font-semibold">Personal details</h2>
        <form [formGroup]="form" (ngSubmit)="save()" class="flex flex-col gap-1" novalidate>
          <mat-form-field><mat-label>Full name</mat-label><input matInput formControlName="full_name" autocomplete="name" />
            <mat-error><app-field-error [control]="form.controls.full_name" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Email</mat-label><input matInput type="email" formControlName="email" autocomplete="email" />
            <mat-error><app-field-error [control]="form.controls.email" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Phone</mat-label><input matInput formControlName="phone" autocomplete="tel" /></mat-form-field>
          <div><button mat-flat-button type="submit" [disabled]="busy() || form.pristine">Save details</button></div>
        </form>
      </section>
      <section class="rounded-2xl border border-outline-variant p-4">
        <h2 class="m-0 mb-1 text-base font-semibold">Change password</h2>
        <p class="m-0 mb-4 text-sm text-on-surface-variant">Signs you out on every other device.</p>
        <app-password-form [save]="changePassword" (changed)="toast.success('Password updated')" />
      </section>
    </div>
  `,
})
export class TenantProfilePage {
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly toast = inject(ToastService);
  private readonly api = inject(AccountApi);
  readonly busy = signal(false);
  readonly form = inject(FormBuilder).nonNullable.group({
    full_name: ['', [Validators.required, Validators.maxLength(200)]],
    email: ['', Validators.email],
    phone: [''],
  });
  readonly changePassword = (current: string, next: string) => this.api.changePassword(current, next);

  constructor() {
    effect(() => {
      const u = this.auth.user();
      if (u) this.form.reset({ full_name: u.full_name, email: u.email ?? '', phone: u.phone ?? '' });
    });
  }

  save(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    this.busy.set(true);
    this.api.updateProfile({ full_name: v.full_name, email: v.email || null, phone: v.phone || null }).subscribe({
      next: () => {
        this.busy.set(false);
        this.toast.success('Profile updated');
        void this.auth.reloadProfile();
      },
      error: (e) => (this.busy.set(false), this.toast.error(e)),
    });
  }
}

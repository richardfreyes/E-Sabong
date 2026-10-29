import { ChangeDetectionStrategy, Component, inject, input, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { Router } from '@angular/router';

import { AuthLayoutComponent, FieldErrorComponent } from '@shared/components';
import { ApiError, AuthStore } from '@shared/services';

@Component({
  selector: 'app-owner-login',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule, AuthLayoutComponent, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-auth-layout appName="Platform Control" headline="Operate every workspace from one console."
                     subline="Tenants, subscriptions, licensing and dedicated deployments." footer="Restricted to platform staff.">
      <h2 class="m-0 text-2xl font-semibold">Sign in</h2>
      <p class="m-0 mb-6 mt-1 text-sm text-on-surface-variant">Use your platform staff account.</p>
      @if (reason() === 'expired') {
        <p class="m-0 mb-4 rounded-lg bg-secondary-container p-3 text-sm text-on-secondary-container" role="status">Your session ended. Sign in again.</p>
      }
      <form [formGroup]="form" (ngSubmit)="submit()" class="flex flex-col gap-1" novalidate>
        <mat-form-field>
          <mat-label>Email</mat-label>
          <input matInput type="email" formControlName="email" autocomplete="username" />
          <mat-error><app-field-error [control]="form.controls.email" /></mat-error>
        </mat-form-field>
        <mat-form-field>
          <mat-label>Password</mat-label>
          <input matInput type="password" formControlName="password" autocomplete="current-password" />
          <mat-error><app-field-error [control]="form.controls.password" /></mat-error>
        </mat-form-field>
        @if (error()) {
          <p class="m-0 mb-3 text-sm text-error" role="alert">{{ error() }}</p>
        }
        <button mat-flat-button type="submit" class="!h-11" [disabled]="busy()" [attr.aria-busy]="busy()">{{ busy() ? 'Signing in…' : 'Sign in' }}</button>
      </form>
    </app-auth-layout>
  `,
})
export class LoginPage {
  readonly returnUrl = input<string>('/');
  readonly reason = input<string | null>(null);
  private readonly auth = inject(AuthStore);
  private readonly router = inject(Router);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', Validators.required],
  });

  async submit(): Promise<void> {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.auth.login(this.form.getRawValue());
      await this.router.navigateByUrl(this.returnUrl() || '/');
    } catch (err) {
      this.error.set((err as ApiError).message);
    } finally {
      this.busy.set(false);
    }
  }
}

import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { AbstractControl, FormBuilder, ReactiveFormsModule, ValidationErrors, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { Router, RouterLink } from '@angular/router';

import { AuthLayoutComponent, FieldErrorComponent } from '@shared/components';
import type { TenantProfile } from '@shared/models';
import { AccountApi, AuthStore, BrandingService, toApiError } from '@shared/services';

function strong(c: AbstractControl): ValidationErrors | null {
  const v = String(c.value ?? '');
  return v && [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(v)).length < 3
    ? { server: 'Mix at least three of: lowercase, uppercase, digits, symbols' } : null;
}

@Component({
  selector: 'app-player-register',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule, MatCheckboxModule, AuthLayoutComponent, FieldErrorComponent, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-auth-layout [appName]="branding.appName()" [logoUrl]="branding.logoUrl()" headline="Create your account"
                     [subline]="branding.config()?.branding?.login_subtitle ?? null">
      <h2 class="m-0 text-2xl font-semibold">Register</h2>
      @if (!branding.config()?.allow_self_registration) {
        <p class="m-0 mt-2 text-sm text-on-surface-variant" role="status">Registration is by invitation. Ask an agent or administrator for an account.</p>
        <a routerLink="/login" class="mt-4 inline-block text-primary">Back to sign in</a>
      } @else {
        <p class="m-0 mb-6 mt-1 text-sm text-on-surface-variant">Already registered? <a routerLink="/login" class="text-primary">Sign in</a></p>
        <form [formGroup]="form" (ngSubmit)="submit()" class="flex flex-col gap-1" novalidate>
          <mat-form-field><mat-label>Username</mat-label><input matInput formControlName="username" autocomplete="username" />
            <mat-hint>Letters, digits, dot, dash or underscore</mat-hint><mat-error><app-field-error [control]="form.controls.username" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Full name</mat-label><input matInput formControlName="full_name" autocomplete="name" />
            <mat-error><app-field-error [control]="form.controls.full_name" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Email (optional)</mat-label><input matInput type="email" formControlName="email" autocomplete="email" />
            <mat-error><app-field-error [control]="form.controls.email" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Password</mat-label><input matInput type="password" formControlName="password" autocomplete="new-password" />
            <mat-error><app-field-error [control]="form.controls.password" /></mat-error></mat-form-field>
          <mat-checkbox formControlName="ack" class="mb-3">I understand demonstration points have no monetary value and cannot be redeemed.</mat-checkbox>
          @if (error()) { <p class="m-0 mb-3 text-sm text-error" role="alert">{{ error() }}</p> }
          <button mat-flat-button type="submit" class="!h-11" [disabled]="busy()">{{ busy() ? 'Creating account…' : 'Create account' }}</button>
        </form>
      }
    </app-auth-layout>
  `,
})
export class RegisterPage {
  readonly branding = inject(BrandingService);
  private readonly api = inject(AccountApi);
  private readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  private readonly router = inject(Router);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).nonNullable.group({
    username: ['', [Validators.required, Validators.minLength(3), Validators.maxLength(64), Validators.pattern(/^[A-Za-z0-9_.-]+$/)]],
    full_name: ['', [Validators.required, Validators.maxLength(200)]],
    email: ['', Validators.email],
    password: ['', [Validators.required, Validators.minLength(10), Validators.maxLength(128), strong]],
    ack: [false, Validators.requiredTrue],
  });

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const { ack: _ack, ...v } = this.form.getRawValue();
    this.busy.set(true);
    this.error.set(null);
    this.api.register({ ...v, email: v.email || null }).subscribe({
      next: (res) => {
        this.auth.acceptSession(res);
        void this.router.navigateByUrl('/');
      },
      error: (e) => (this.busy.set(false), this.error.set(toApiError(e).message)),
    });
  }
}

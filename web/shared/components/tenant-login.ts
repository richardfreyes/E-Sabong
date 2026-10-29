import { ChangeDetectionStrategy, Component, inject, input, output, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import type { TenantProfile } from '@shared/models';
import { ApiError } from '../services/api-client';
import { AuthStore } from '../services/auth.store';
import { BrandingService } from '../services/branding.service';
import { AuthLayoutComponent } from './shell';
import { FieldErrorComponent } from './primitives';

/** Branded sign-in for tenant users (admin and player portals). */
@Component({
  selector: 'app-tenant-login',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule, AuthLayoutComponent, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @let b = branding.config()?.branding;
    <app-auth-layout [appName]="branding.appName()" [logoUrl]="branding.logoUrl()" [backgroundUrl]="b?.login_background_url ?? null"
                     [headline]="b?.login_title || defaultHeadline()" [subline]="b?.login_subtitle ?? null" [footer]="b?.footer_text ?? ''">
      <h2 class="m-0 text-2xl font-semibold">{{ heading() }}</h2>
      <p class="m-0 mb-6 mt-1 text-sm text-on-surface-variant">{{ hint() }}</p>
      @if (branding.suspended()) {
        <p class="m-0 mb-4 rounded-lg bg-error-container p-3 text-sm text-on-error-container" role="alert">
          This workspace is currently unavailable. Contact {{ b?.contact_email || 'your provider' }}.</p>
      }
      @if (notice()) {
        <p class="m-0 mb-4 rounded-lg bg-secondary-container p-3 text-sm text-on-secondary-container" role="status">{{ notice() }}</p>
      }
      <form [formGroup]="form" (ngSubmit)="submit()" class="flex flex-col gap-1" novalidate>
        <mat-form-field>
          <mat-label>Username or email</mat-label>
          <input matInput formControlName="username" autocomplete="username" />
          <mat-error><app-field-error [control]="form.controls.username" /></mat-error>
        </mat-form-field>
        <mat-form-field>
          <mat-label>Password</mat-label>
          <input matInput type="password" formControlName="password" autocomplete="current-password" />
          <mat-error><app-field-error [control]="form.controls.password" /></mat-error>
        </mat-form-field>
        @if (error()) {
          <p class="m-0 mb-3 text-sm text-error" role="alert">{{ error() }}</p>
        }
        <button mat-flat-button type="submit" class="!h-11" [disabled]="busy() || branding.suspended()" [attr.aria-busy]="busy()">{{ busy() ? 'Signing in…' : 'Sign in' }}</button>
      </form>
      <ng-content />
    </app-auth-layout>
  `,
})
export class TenantLoginComponent {
  readonly heading = input('Sign in');
  readonly hint = input('Enter the credentials your administrator gave you.');
  readonly defaultHeadline = input('Welcome back');
  readonly notice = input<string | null>(null);
  /** Return an error message to refuse the session (e.g. no admin access). */
  readonly accept = input<(user: TenantProfile) => string | null>(() => null);
  readonly loggedIn = output<TenantProfile>();

  readonly branding = inject(BrandingService);
  private readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).nonNullable.group({
    username: ['', Validators.required],
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
      const user = await this.auth.login(this.form.getRawValue());
      const refusal = this.accept()(user);
      if (refusal) {
        await this.auth.logout(false);
        this.error.set(refusal);
        return;
      }
      this.loggedIn.emit(user);
    } catch (err) {
      this.error.set((err as ApiError).message);
    } finally {
      this.busy.set(false);
    }
  }
}

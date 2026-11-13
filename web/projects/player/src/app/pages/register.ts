import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { AuthLayoutComponent, FieldErrorComponent, PASSWORD_MIN_LENGTH, passwordPolicy } from '@shared/components';
import type { TenantProfile } from '@shared/models';
import { AuthStore, BrandingService, toApiError } from '@shared/services';

@Component({
  selector: 'app-player-register',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule, MatCheckboxModule, AuthLayoutComponent, FieldErrorComponent, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-auth-layout [appName]="branding.appName()" [logoUrl]="branding.logoUrl()" headline="Create your account"
                     [subline]="branding.config()?.branding?.login_subtitle ?? null">
      <h2 class="m-0 text-2xl font-semibold">{{ asAgent ? (applicationRole === 'sub_agent' ? 'Apply as a Sub-Agent' : 'Apply as an agent') : 'Register' }}</h2>
      @if (!branding.config()?.allow_self_registration && !referrer) {
        <p class="m-0 mt-2 text-sm text-on-surface-variant" role="status">Registration is by invitation. Ask an agent or administrator for an account.</p>
        <a routerLink="/login" class="mt-4 inline-block text-primary">Back to sign in</a>
      } @else {
        <p class="m-0 mb-6 mt-1 text-sm text-on-surface-variant">Already registered? <a routerLink="/login" class="text-primary">Sign in</a></p>
        @if (referrer) {
          <p class="m-0 mb-4 rounded-lg border border-outline-variant px-3 py-2 text-sm" role="status" data-testid="referrer">
            @if (asAgent) { {{ applicationRole === 'sub_agent' ? 'Sub-Agent' : 'Agent' }} application from <b>{{ referrer }}</b>. You will have your own dashboard under this account. }
            @else { Referred by <b>{{ referrer }}</b>. Your account will be added under this agent. }</p>
        }
        <form [formGroup]="form" (ngSubmit)="submit()" class="flex flex-col gap-1" novalidate>
          <mat-form-field><mat-label>Username</mat-label><input matInput formControlName="username" autocomplete="username" />
            <mat-hint>Letters, digits, dot, dash or underscore</mat-hint><mat-error><app-field-error [control]="form.controls.username" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Full name</mat-label><input matInput formControlName="full_name" autocomplete="name" />
            <mat-error><app-field-error [control]="form.controls.full_name" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Email (optional)</mat-label><input matInput type="email" formControlName="email" autocomplete="email" />
            <mat-error><app-field-error [control]="form.controls.email" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Password</mat-label><input matInput type="password" formControlName="password" autocomplete="new-password" />
            <mat-hint>At least {{ minLength }} characters</mat-hint><mat-error><app-field-error [control]="form.controls.password" /></mat-error></mat-form-field>
          <mat-checkbox formControlName="ack" class="mb-3">I understand my balance has no cash value and cannot be bought, transferred or redeemed.</mat-checkbox>
          @if (error()) { <p class="m-0 mb-3 text-sm text-error" role="alert">{{ error() }}</p> }
          <button mat-flat-button type="submit" class="!h-11" [disabled]="busy()">{{ busy() ? 'Creating account…' : 'Create account' }}</button>
        </form>
      }
    </app-auth-layout>
  `,
})
export class RegisterPage {
  readonly branding = inject(BrandingService);
  private readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  private readonly router = inject(Router);
  /** Agent username from a referral link (`/register?ref=`); the server checks it and assigns the player. */
  private readonly query = inject(ActivatedRoute).snapshot.queryParamMap;
  readonly referrer = (this.query.get('ref') ?? '').trim().slice(0, 64) || null;
  /** The master agent's Agent Application Link (`&apply=agent`) creates an agent account. */
  readonly applicationRole = this.query.get('apply') === 'sub_agent' ? 'sub_agent' : 'agent';
  readonly asAgent = !!this.referrer && ['agent', 'sub_agent'].includes(this.query.get('apply') ?? '');
  readonly minLength = PASSWORD_MIN_LENGTH;
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).nonNullable.group({
    username: ['', [Validators.required, Validators.minLength(3), Validators.maxLength(64), Validators.pattern(/^[A-Za-z0-9_.-]+$/)]],
    full_name: ['', [Validators.required, Validators.maxLength(200)]],
    email: ['', Validators.email],
    password: ['', [Validators.required, passwordPolicy]],
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
    this.auth.register({ ...v, email: v.email || null, ...(this.referrer ? { ref: this.referrer } : {}), ...(this.asAgent ? { apply: this.applicationRole } : {}) }).then(
      () => void this.router.navigateByUrl('/'),
      (e: unknown) => (this.busy.set(false), this.error.set(toApiError(e).message)),
    );
  }
}

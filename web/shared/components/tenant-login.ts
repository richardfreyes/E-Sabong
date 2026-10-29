import { ChangeDetectionStrategy, Component, inject, input, output, signal } from '@angular/core';
import { FormBuilder, Validators } from '@angular/forms';

import type { TenantProfile } from '@shared/models';
import { ApiError } from '../services/api-client';
import { AuthStore } from '../services/auth.store';
import { BrandingService } from '../services/branding.service';
import { LoginLayoutComponent } from './login-layout';

/** Branded sign-in for tenant users (admin and player portals). */
@Component({
  selector: 'app-tenant-login',
  imports: [LoginLayoutComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @let b = branding.config()?.branding;
    <app-login-layout [appName]="branding.appName()" [logoUrl]="branding.logoUrl()" [portalName]="portalName()" [splash]="splash()"
                      [username]="form.controls.username" [password]="form.controls.password"
                      [busy]="busy()" [disabled]="branding.suspended()" [error]="error()" (submitted)="submit()">
      @if (branding.suspended()) {
        <p loginNotice class="login-notice is-error" role="alert">
          This workspace is currently unavailable. Contact {{ b?.contact_email || 'your provider' }}.</p>
      }
      @if (notice()) {
        <p loginNotice class="login-notice" role="status">{{ notice() }}</p>
      }
      <ng-content />
    </app-login-layout>
  `,
})
export class TenantLoginComponent {
  /** Shown under the logo; omit for the standard player sign-in. */
  readonly portalName = input<string | null>(null);
  /** Play the one-time logo reveal before the form. */
  readonly splash = input(false);
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

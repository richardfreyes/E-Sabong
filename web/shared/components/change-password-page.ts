import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Router } from '@angular/router';

import { AccountApi } from '../services/tenant-api';
import { AuthStore } from '../services/auth.store';
import { BrandingService } from '../services/branding.service';
import { PasswordFormComponent } from './password-form';
import { AuthLayoutComponent } from './shell';

/** Shown after signing in with a temporary password; nothing else is reachable until it is changed. */
@Component({
  selector: 'app-change-password',
  imports: [AuthLayoutComponent, PasswordFormComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-auth-layout [appName]="branding.appName()" [logoUrl]="branding.logoUrl()" headline="Secure your account">
      <h2 class="m-0 text-2xl font-semibold">Choose a new password</h2>
      <p class="m-0 mb-6 mt-1 text-sm text-on-surface-variant">You signed in with a temporary password. Replace it to continue.</p>
      <app-password-form [save]="save" submitLabel="Save and continue" (changed)="done()" />
      <button type="button" class="mt-4 w-full border-0 bg-transparent text-sm text-primary" (click)="auth.logout()">Sign out instead</button>
    </app-auth-layout>
  `,
})
export class ChangePasswordPage {
  readonly branding = inject(BrandingService);
  readonly auth = inject(AuthStore);
  private readonly api = inject(AccountApi);
  private readonly router = inject(Router);
  readonly save = (current: string, next: string) => this.api.changePassword(current, next);

  async done(): Promise<void> {
    this.auth.passwordChanged();
    await this.auth.reloadProfile();
    await this.router.navigateByUrl('/');
  }
}

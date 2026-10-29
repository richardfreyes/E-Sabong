import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';
import { Router } from '@angular/router';

import { TenantLoginComponent } from '@shared/components';
import type { TenantProfile } from '@shared/models';

@Component({
  selector: 'app-admin-login',
  imports: [TenantLoginComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-tenant-login heading="Administration" hint="Administrators, declarators, operators and agents sign in here."
                      defaultHeadline="Run events, manage your network, and keep records straight."
                      [notice]="reason() === 'expired' ? 'Your session ended. Sign in again.' : null"
                      [accept]="accept" (loggedIn)="done($event)">
      <p class="m-0 mt-6 text-center text-sm text-on-surface-variant">Player? <a href="/" class="text-primary">Go to the player portal</a></p>
    </app-tenant-login>
  `,
})
export class LoginPage {
  readonly returnUrl = input<string>('/');
  readonly reason = input<string | null>(null);
  private readonly router = inject(Router);

  readonly accept = (user: TenantProfile): string | null =>
    user.permissions.length || user.must_change_password ? null : 'This account has no administration access. Use the player portal instead.';

  async done(user: TenantProfile): Promise<void> {
    await this.router.navigateByUrl(user.must_change_password ? '/change-password' : this.returnUrl() || '/');
  }
}

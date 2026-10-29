import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { TenantLoginComponent } from '@shared/components';
import type { TenantProfile } from '@shared/models';
import { BrandingService } from '@shared/services';

@Component({
  selector: 'app-player-login',
  imports: [TenantLoginComponent, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-tenant-login [notice]="reason() === 'expired' ? 'Your session ended. Sign in again.' : null" (loggedIn)="done($event)">
      @if (branding.config()?.allow_self_registration) {
        <p class="login-note">New here? <a routerLink="/register">Create an account</a></p>
      }
    </app-tenant-login>
  `,
})
export class LoginPage {
  readonly returnUrl = input<string>('/');
  readonly reason = input<string | null>(null);
  readonly branding = inject(BrandingService);
  private readonly router = inject(Router);

  async done(user: TenantProfile): Promise<void> {
    await this.router.navigateByUrl(user.must_change_password ? '/change-password' : this.returnUrl() || '/');
  }
}

import { ChangeDetectionStrategy, Component, inject } from '@angular/core';

import { PageHeaderComponent, PasswordFormComponent } from '@shared/components';
import type { PlatformProfile } from '@shared/models';
import { LabelPipe } from '@shared/pipes';
import { AuthStore, PlatformApi, ToastService } from '@shared/services';

@Component({
  selector: 'app-owner-account',
  imports: [PageHeaderComponent, PasswordFormComponent, LabelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Account" subtitle="Your platform staff profile and password." />
    <div class="grid gap-6 lg:grid-cols-2">
      <section class="rounded-2xl border border-outline-variant p-4">
        <h2 class="m-0 mb-3 text-base font-semibold">Profile</h2>
        @if (auth.user(); as u) {
          <dl class="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt class="text-on-surface-variant">Name</dt><dd class="m-0">{{ u.full_name }}</dd>
            <dt class="text-on-surface-variant">Email</dt><dd class="m-0">{{ u.email }}</dd>
            <dt class="text-on-surface-variant">Role</dt><dd class="m-0">{{ u.role | label }}</dd>
          </dl>
        }
      </section>
      <section class="rounded-2xl border border-outline-variant p-4">
        <h2 class="m-0 mb-3 text-base font-semibold">Change password</h2>
        <p class="m-0 mb-4 text-sm text-on-surface-variant">Other signed-in sessions are revoked when you change your password.</p>
        <app-password-form [save]="save" (changed)="toast.success('Password updated')" />
      </section>
    </div>
  `,
})
export class AccountPage {
  readonly auth = inject<AuthStore<PlatformProfile>>(AuthStore);
  readonly toast = inject(ToastService);
  private readonly api = inject(PlatformApi);
  readonly save = (current: string, next: string) => this.api.changePassword(current, next);
}

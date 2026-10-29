import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { AppShellComponent, NavItem } from '@shared/components';
import type { PlatformProfile } from '@shared/models';
import { LabelPipe } from '@shared/pipes';
import { AuthStore } from '@shared/services';

const NAV: NavItem[] = [
  { label: 'Overview', icon: 'monitoring', route: '/', exact: true },
  { label: 'Tenants', icon: 'domain', route: '/tenants', section: 'Customers' },
  { label: 'Signup requests', icon: 'how_to_reg', route: '/signups' },
  { label: 'Subscriptions', icon: 'autorenew', route: '/subscriptions', section: 'Billing' },
  { label: 'Invoices', icon: 'receipt_long', route: '/invoices' },
  { label: 'Plans', icon: 'sell', route: '/plans' },
  { label: 'Licenses', icon: 'key', route: '/licenses', section: 'Dedicated' },
  { label: 'Deployments', icon: 'dns', route: '/deployments' },
  { label: 'Feature flags', icon: 'toggle_on', route: '/feature-flags', section: 'Platform' },
  { label: 'Audit log', icon: 'policy', route: '/audit' },
];

@Component({
  selector: 'app-owner-layout',
  imports: [AppShellComponent],
  providers: [LabelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-shell appName="Platform Control" contextLabel="SaaS owner console" [navItems]="nav" [userName]="user()?.full_name ?? ''"
               [userSubtitle]="role()" profileRoute="/account" (logout)="auth.logout()" />
  `,
})
export class OwnerLayout {
  readonly auth = inject<AuthStore<PlatformProfile>>(AuthStore);
  private readonly label = inject(LabelPipe);
  readonly nav = NAV;
  readonly user = this.auth.user;
  readonly role = computed(() => this.label.transform(this.user()?.role));
}

import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { AppShellComponent, NavItem } from '@shared/components';
import type { ModuleKey, TenantProfile } from '@shared/models';
import { LabelPipe } from '@shared/pipes';
import { AuthStore, BrandingService, RealtimeService } from '@shared/services';

interface AdminNav extends NavItem {
  permissions?: string[];
  module?: ModuleKey;
  modules?: ModuleKey[];
}

const NAV: AdminNav[] = [
  { label: 'Dashboard', icon: 'space_dashboard', route: '/', exact: true, permissions: ['dashboard.view'] },
  { label: 'Accounts', icon: 'group', route: '/users', section: 'People', permissions: ['users.view_all', 'users.view_downline', 'users.manage'] },
  { label: 'Hierarchy', icon: 'account_tree', route: '/hierarchy', permissions: ['users.view_all', 'users.view_downline', 'users.manage'] },
  { label: 'Roles', icon: 'admin_panel_settings', route: '/roles', permissions: ['roles.manage'] },
  { label: 'Declarator', icon: 'fact_check', route: '/declarator', section: 'Operations', modules: ['events', 'matches'], permissions: ['results.submit', 'results.approve'] },
  { label: 'Events', icon: 'event', route: '/events', section: 'Operations', module: 'events', permissions: ['events.view', 'events.manage', 'matches.manage'] },
  { label: 'Demo points', icon: 'toll', route: '/points', module: 'points', permissions: ['points.view_all', 'points.view_downline'] },
  { label: 'Reports', icon: 'bar_chart', route: '/reports', module: 'reports', permissions: ['reports.view'] },
  { label: 'Audit log', icon: 'policy', route: '/audit', section: 'Workspace', permissions: ['audit.view'] },
  { label: 'Settings', icon: 'tune', route: '/settings', permissions: ['settings.manage', 'branding.manage'] },
  { label: 'Subscription', icon: 'credit_card', route: '/subscription', permissions: ['billing.view'] },
];

@Component({
  selector: 'app-admin-layout',
  imports: [AppShellComponent],
  providers: [LabelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-shell [appName]="branding.appName()" [logoUrl]="branding.logoUrl()" [contextLabel]="user()?.account_type === 'declarator' ? 'Declarator workspace' : 'Administration'" [navItems]="nav()"
               [userName]="user()?.full_name ?? ''" [userSubtitle]="subtitle()" profileRoute="/profile" (logout)="auth.logout()">
      <a toolbar href="/" class="mr-2 hidden text-sm text-primary no-underline sm:inline">Open player portal</a>
    </app-shell>
  `,
})
export class AdminLayout {
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly branding = inject(BrandingService);
  private readonly label = inject(LabelPipe);
  readonly user = this.auth.user;
  readonly subtitle = computed(() => this.label.transform(this.user()?.account_type));

  constructor() {
    if (this.branding.config()?.feature_flags?.['live_updates'] !== false) inject(RealtimeService).connect();
  }

  readonly nav = computed(() => {
    let section: string | undefined;
    const visible = NAV.filter(
      (n) => (!n.permissions || this.auth.hasAnyPermission(...n.permissions)) && (!n.module || this.branding.hasModule(n.module)) && (!n.modules || n.modules.every(m => this.branding.hasModule(m))),
    );
    // Show a section heading only on the first visible item of that section.
    return visible.map((n) => {
      const out = { ...n, section: n.section && n.section !== section ? n.section : undefined };
      if (n.section) section = n.section;
      return out;
    });
  });
}

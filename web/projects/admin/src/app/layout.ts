import { ChangeDetectionStrategy, Component, DestroyRef, Injector, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatBadgeModule } from '@angular/material/badge';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { RouterLink } from '@angular/router';

import { AppShellComponent, NavItem } from '@shared/components';
import type { AppNotification, ModuleKey, TenantProfile } from '@shared/models';
import { LabelPipe } from '@shared/pipes';
import { AuthStore, BrandingService, PortalApi, RealtimeService, ToastService } from '@shared/services';

import { AgentFrame, isAgentAccount } from '@shared/agent';

interface AdminNav extends NavItem {
  permissions?: string[];
  accountTypes?: string[];
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
  { label: 'Live streaming', icon: 'videocam', route: '/streams', module: 'events', permissions: ['settings.manage'] },
  { label: 'Stream Operators', icon: 'video_camera_front', route: '/operators', module: 'events', permissions: ['settings.manage'] },
  { label: 'Operator console', icon: 'live_tv', route: '/operator', module: 'events', permissions: ['streams.operate'] },
  { label: 'Betting', icon: 'casino', route: '/betting', modules: ['events', 'matches'], permissions: ['events.view', 'events.manage', 'matches.manage', 'points.view_all'] },
  { label: 'Balance History', icon: 'receipt_long', route: '/balance-history', modules: ['events', 'matches'], permissions: ['points.view_all'] },
  { label: 'Balance Management', icon: 'account_balance_wallet', route: '/balance-management', modules: ['events', 'matches'], permissions: ['points.view_all', 'points.view_downline', 'balance.manage', 'loader.view'] },
  { label: 'Balance Requests', icon: 'approval', route: '/balance-requests', modules: ['events', 'matches'], permissions: ['balance.manage', 'loader.view'] },
  { label: 'Legacy points archive', icon: 'toll', route: '/points', module: 'points', permissions: ['points.view_all', 'points.view_downline'] },
  { label: 'Reports', icon: 'bar_chart', route: '/reports', module: 'reports', permissions: ['reports.view'] },
  { label: 'Audit log', icon: 'policy', route: '/audit', section: 'Workspace', permissions: ['audit.view'] },
  { label: 'Settings', icon: 'tune', route: '/settings', permissions: ['settings.manage', 'branding.manage'] },
  { label: 'Subscription', icon: 'credit_card', route: '/subscription', permissions: ['billing.view'] },
];

@Component({
  selector: 'app-admin-layout',
  imports: [AppShellComponent, AgentFrame, MatBadgeModule, MatButtonModule, MatIconModule, MatMenuModule, RouterLink],
  providers: [LabelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (agentWorkspace()) {
      <app-agent-frame [navItems]="nav()" />
    } @else {
      <app-shell [appName]="branding.appName()" [logoUrl]="branding.logoUrl()" [contextLabel]="contextLabel()" [navItems]="nav()"
                 [userName]="user()?.full_name ?? ''" [userSubtitle]="subtitle()" profileRoute="/profile" (logout)="auth.logout()">
        <ng-container toolbar>
          @if (approver()) {
            <button mat-icon-button type="button" [matMenuTriggerFor]="approvalNotes" (menuOpened)="loadNotes()"
                    [attr.aria-label]="unread() ? unread() + ' notifications to approve' : 'Notifications'">
              <mat-icon [matBadge]="unread() || null" matBadgeSize="small" matBadgeColor="warn" aria-hidden="false">notifications</mat-icon>
            </button>
            <mat-menu #approvalNotes="matMenu">
              @for (note of notes(); track note.id) {
                <a mat-menu-item routerLink="/balance-requests" (click)="read(note)">{{ note.title }}</a>
              } @empty {
                <button mat-menu-item type="button" disabled>No approval requests</button>
              }
            </mat-menu>
          }
          @if (user()?.account_type !== 'loader') {
            <a href="/" class="mr-2 hidden text-sm text-primary no-underline sm:inline">Open player portal</a>
          }
        </ng-container>
      </app-shell>
    }
  `,
})
export class AdminLayout {
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly branding = inject(BrandingService);
  private readonly label = inject(LabelPipe);
  readonly user = this.auth.user;
  readonly approver = computed(() => {
    const type = this.user()?.account_type;
    return type === 'admin' || type === 'loader';
  });
  readonly unread = signal(0);
  readonly notes = signal<AppNotification[]>([]);
  private readonly destroy = inject(DestroyRef);
  private readonly injector = inject(Injector);
  private watching = false;
  readonly subtitle = computed(() => this.label.transform(this.user()?.account_type));
  readonly contextLabel = computed(() => {
    const type = this.user()?.account_type;
    if (type === 'declarator') return 'Declarator workspace';
    if (type === 'loader') return 'Loader workspace';
    return 'Administration';
  });
  readonly agentWorkspace = computed(() => isAgentAccount(this.user()?.account_type));

  constructor() {
    const realtime = inject(RealtimeService);
    if (this.branding.config()?.feature_flags?.['live_updates'] !== false) realtime.connect();
    effect(() => {
      if (!this.approver() || this.watching) return;
      this.watching = true;
      const portal = this.injector.get(PortalApi);
      const toast = this.injector.get(ToastService);
      realtime.on<{ id: string; type: string; title: string; body: string | null }>('notification.created')
        .pipe(takeUntilDestroyed(this.destroy))
        .subscribe((note) => {
          if (note.type !== 'balance.approval_required') return;
          this.unread.update((count) => count + 1);
          this.notes.update((list) => [
            { id: note.id, type: note.type, title: note.title, body: note.body, data: {}, read_at: null, created_at: new Date().toISOString() },
            ...list,
          ].slice(0, 8));
          toast.info(note.title);
        });
      this.loadNotes();
    });
  }

  loadNotes(): void {
    if (!this.approver()) return;
    this.injector.get(PortalApi).notifications({ page_size: 20 }).subscribe({
      next: (page) => {
        const approval = page.items.filter((note) => note.type === 'balance.approval_required');
        this.notes.set(approval.slice(0, 8));
        this.unread.set(approval.filter((note) => !note.read_at).length);
      },
      error: () => undefined,
    });
  }

  read(note: AppNotification): void {
    if (note.read_at) return;
    this.injector.get(PortalApi).readNotification(note.id).subscribe({
      next: () => {
        this.notes.update((list) => list.map((item) => item.id === note.id ? { ...item, read_at: new Date().toISOString() } : item));
        this.unread.update((count) => Math.max(0, count - 1));
      },
    });
  }

  readonly nav = computed(() => {
    let section: string | undefined;
    const visible = NAV.filter(
      (n) => (!n.permissions || this.auth.hasAnyPermission(...n.permissions)) && (!n.accountTypes || n.accountTypes.includes(this.user()?.account_type ?? '')) && (!n.module || this.branding.hasModule(n.module)) && (!n.modules || n.modules.every(m => this.branding.hasModule(m))),
    );
    // Show a section heading only on the first visible item of that section.
    return visible.map((n) => {
      const out = { ...n, section: n.section && n.section !== section ? n.section : undefined };
      if (n.section) section = n.section;
      return out;
    });
  });
}

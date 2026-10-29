import { BreakpointObserver } from '@angular/cdk/layout';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { MatBadgeModule } from '@angular/material/badge';
import { MatButtonModule } from '@angular/material/button';
import { MatDividerModule } from '@angular/material/divider';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatTooltipModule } from '@angular/material/tooltip';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';

import type { ModuleKey, TenantProfile } from '@shared/models';
import { PointsPipe, RelativeTimePipe } from '@shared/pipes';
import { AuthStore, BrandingService, RealtimeService, ToastService } from '@shared/services';
import { PlayerPrefs } from './core/player-prefs';
import { PlayerSession } from './core/player-session';
import { ConnectionStatusComponent } from './ui/connection-status';

interface PlayerNav {
  label: string;
  short?: string;
  icon: string;
  route: string;
  exact?: boolean;
  module?: ModuleKey;
  quick?: boolean;
}

const NAV: PlayerNav[] = [
  { label: 'Dashboard', short: 'Arena', icon: 'stadium', route: '/', exact: true, quick: true },
  { label: 'Events', icon: 'event', route: '/events', module: 'events', quick: true },
  { label: 'Match history', short: 'History', icon: 'bubble_chart', route: '/history', module: 'events', quick: true },
  { label: 'Results', icon: 'emoji_events', route: '/results', module: 'events' },
  { label: 'Points history', short: 'Points', icon: 'toll', route: '/points', module: 'points', quick: true },
  { label: 'Notifications', icon: 'notifications', route: '/notifications' },
  { label: 'Profile', icon: 'person', route: '/profile' },
  { label: 'Settings', icon: 'tune', route: '/settings' },
];

@Component({
  selector: 'app-player-layout',
  imports: [
    RouterOutlet, RouterLink, RouterLinkActive, MatSidenavModule, MatIconModule, MatButtonModule, MatMenuModule, MatBadgeModule,
    MatDividerModule, MatTooltipModule, ConnectionStatusComponent, PointsPipe, RelativeTimePipe,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './layout.scss',
  template: `
    <a class="skip" href="#main">Skip to content</a>
    <div class="app">
      <header class="topbar">
        <button mat-icon-button type="button" (click)="toggleNav()" [attr.aria-label]="isMobile() ? 'Open menu' : collapsed() ? 'Expand menu' : 'Collapse menu'"
                [attr.aria-expanded]="isMobile() ? drawerOpen() : !collapsed()">
          <mat-icon>menu</mat-icon>
        </button>
        <a routerLink="/" class="brand" [attr.aria-label]="branding.appName() + ' home'">
          @if (branding.logoUrl(); as logo) { <img [src]="logo" alt="" /> }
          @else { <span class="mark pp-display" aria-hidden="true">{{ initial() }}</span> }
          <span class="brand-name pp-display">{{ branding.appName() }}</span>
        </a>
        <span class="grow"></span>
        <pp-connection-status class="hidden md:block" />
        <pp-connection-status class="md:hidden" [compact]="true" />
        @if (branding.hasModule('points')) {
          <a routerLink="/points" class="balance" [class.bump]="bump()" matTooltip="Demonstration points have no cash value"
             [attr.aria-label]="(session.balance() | points) + ' demonstration points'">
            <mat-icon aria-hidden="true">toll</mat-icon>
            <span class="pp-num">{{ session.balance() | points }}</span>
            <span class="unit">pts</span>
          </a>
        }
        <button mat-icon-button type="button" [matMenuTriggerFor]="notes" (menuOpened)="session.loadRecent()"
                [attr.aria-label]="session.unread() ? session.unread() + ' unread notifications' : 'Notifications'">
          <mat-icon [matBadge]="session.unread() || null" matBadgeSize="small" matBadgeColor="warn" aria-hidden="false">notifications</mat-icon>
        </button>
        <button type="button" class="profile" [matMenuTriggerFor]="profile" aria-label="Account menu">
          <span class="avatar" aria-hidden="true">{{ initials() }}</span>
          <span class="username">{{ auth.user()?.username }}</span>
          <mat-icon aria-hidden="true">expand_more</mat-icon>
        </button>
      </header>

      <mat-menu #notes="matMenu" xPosition="before" class="pp-menu">
        <div class="menu-head" (click)="$event.stopPropagation()" (keydown)="$event.stopPropagation()">
          <span class="font-semibold">Notifications</span>
          @if (session.unread()) { <button mat-button type="button" (click)="session.markAllRead()">Mark all as read</button> }
        </div>
        @for (n of session.recent(); track n.id) {
          <a mat-menu-item routerLink="/notifications" class="note" [class.unread]="!n.read_at">
            <span class="note-title">{{ n.title }}</span>
            <span class="note-time">{{ n.created_at | relativeTime }}</span>
          </a>
        } @empty {
          <p class="menu-empty">You're all caught up.</p>
        }
        <mat-divider />
        <a mat-menu-item routerLink="/notifications">See all notifications</a>
      </mat-menu>

      <mat-menu #profile="matMenu" xPosition="before" class="pp-menu">
        <div class="menu-head menu-id">
          <span class="font-semibold">{{ auth.user()?.full_name }}</span>
          <span class="text-xs text-on-surface-variant">{{ '@' + auth.user()?.username }}</span>
        </div>
        <mat-divider />
        <a mat-menu-item routerLink="/profile"><mat-icon>person</mat-icon>Profile</a>
        <a mat-menu-item routerLink="/settings"><mat-icon>tune</mat-icon>Settings</a>
        <a mat-menu-item routerLink="/activity"><mat-icon>history</mat-icon>Account activity</a>
        <mat-divider />
        <button mat-menu-item type="button" (click)="auth.logout()"><mat-icon>logout</mat-icon>Sign out</button>
      </mat-menu>

      <mat-sidenav-container class="body" autosize>
        <mat-sidenav class="rail" [class.collapsed]="collapsed() && !isMobile()" [mode]="isMobile() ? 'over' : 'side'"
                     [opened]="!isMobile() || drawerOpen()" (closedStart)="drawerOpen.set(false)">
          <nav aria-label="Main">
            @for (item of nav(); track item.route) {
              <a [routerLink]="item.route" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: !!item.exact }" class="nav-link"
                 [matTooltip]="collapsed() && !isMobile() ? item.label : ''" matTooltipPosition="right">
                <mat-icon aria-hidden="true">{{ item.icon }}</mat-icon>
                <span class="nav-label">{{ item.label }}</span>
              </a>
            }
          </nav>
          @if (!collapsed() || isMobile()) {
            <p class="rail-note">Demo points have no cash value and cannot be redeemed.</p>
          }
        </mat-sidenav>
        <mat-sidenav-content class="content">
          <main id="main" tabindex="-1"><router-outlet /></main>
        </mat-sidenav-content>
      </mat-sidenav-container>

      <nav class="bottom-nav" aria-label="Quick links">
        @for (item of quick(); track item.route) {
          <a [routerLink]="item.route" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: !!item.exact }">
            <mat-icon aria-hidden="true">{{ item.icon }}</mat-icon><span>{{ item.short ?? item.label }}</span>
          </a>
        }
        <button type="button" (click)="drawerOpen.set(true)" [attr.aria-expanded]="drawerOpen()">
          <mat-icon aria-hidden="true">more_horiz</mat-icon><span>More</span>
        </button>
      </nav>
    </div>
  `,
})
export class PlayerLayout {
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly branding = inject(BrandingService);
  readonly session = inject(PlayerSession);
  private readonly prefs = inject(PlayerPrefs);
  private readonly realtime = inject(RealtimeService);
  private readonly toast = inject(ToastService);

  readonly drawerOpen = signal(false);
  readonly collapsed = this.prefs.railCollapsed;
  readonly isMobile = toSignal(inject(BreakpointObserver).observe('(max-width: 1023px)').pipe(map((r) => r.matches)), { initialValue: false });
  readonly nav = computed(() => NAV.filter((n) => !n.module || this.branding.hasModule(n.module)));
  readonly quick = computed(() => this.nav().filter((n) => n.quick));
  readonly initial = computed(() => this.branding.appName().slice(0, 1).toUpperCase());
  readonly initials = computed(() => {
    const name = this.auth.user()?.full_name ?? this.auth.user()?.username ?? '?';
    return name.split(/\s+/).map((p) => p[0]).join('').slice(0, 2).toUpperCase();
  });
  readonly bump = signal(false);

  constructor() {
    this.session.load();
    // Live updates follow the player's preference and the workspace flag.
    effect(() => {
      const allowed = this.branding.config()?.feature_flags?.['live_updates'] !== false;
      if (this.prefs.liveUpdates() && allowed) this.realtime.connect();
      else this.realtime.disconnect();
    });
    effect(() => {
      if (this.session.balancePulse() === 0) return;
      this.bump.set(true);
      setTimeout(() => this.bump.set(false), 900);
    });
    const destroy = inject(DestroyRef);
    this.realtime.on<{ title: string }>('notification.created').pipe(takeUntilDestroyed(destroy)).subscribe((n) => this.toast.info(n.title));
    inject(Router).events.pipe(filter((e) => e instanceof NavigationEnd), takeUntilDestroyed(destroy)).subscribe(() => this.drawerOpen.set(false));
  }

  toggleNav(): void {
    if (this.isMobile()) this.drawerOpen.update((v) => !v);
    else this.prefs.update({ railCollapsed: !this.collapsed() });
  }
}

import { BreakpointObserver } from '@angular/cdk/layout';
import { ChangeDetectionStrategy, Component, computed, inject, input, output, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatDividerModule } from '@angular/material/divider';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatTooltipModule } from '@angular/material/tooltip';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';

import { ThemeService } from '../services/theme.service';

export interface NavItem {
  label: string;
  icon: string;
  route: string;
  exact?: boolean;
  section?: string;
}

@Component({
  selector: 'app-theme-toggle',
  imports: [MatButtonModule, MatIconModule, MatTooltipModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button mat-icon-button type="button" (click)="theme.toggle()" [matTooltip]="label()" [attr.aria-label]="label()">
      <mat-icon>{{ theme.isDark() ? 'light_mode' : 'dark_mode' }}</mat-icon>
    </button>
  `,
})
export class ThemeToggleComponent {
  readonly theme = inject(ThemeService);
  readonly label = computed(() => (this.theme.isDark() ? 'Switch to light mode' : 'Switch to dark mode'));
}

/** Responsive application frame: side navigation on desktop, drawer on phones. */
@Component({
  selector: 'app-shell',
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    MatSidenavModule,
    MatIconModule,
    MatButtonModule,
    MatMenuModule,
    MatDividerModule,
    ThemeToggleComponent, MatTooltipModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './shell.scss',
  template: `
    <a class="skip rounded-md bg-primary px-3 py-2 text-on-primary" href="#main">Skip to content</a>
    <mat-sidenav-container class="shell" autosize>
      <mat-sidenav [class.compact]="collapsed() && !isMobile()" class="sidebar"
                   [mode]="isMobile() ? 'over' : 'side'" [opened]="!isMobile() || drawerOpen()" (closedStart)="drawerOpen.set(false)">
        <div class="brand">
          @if (logoUrl()) { <img [src]="logoUrl()" alt="" /> }
          @else { <span class="brand-mark" aria-hidden="true">{{ appName().slice(0, 1) }}</span> }
          <div class="brand-copy"><span class="brand-name">{{ appName() }}</span><span class="brand-context">{{ contextLabel() }}</span></div>
        </div>
        <nav aria-label="Main">
          @for (item of navItems(); track item.route) {
            @if (item.section) { <div class="section-label">{{ item.section }}</div> }
            <a class="nav-link" [routerLink]="item.route" routerLinkActive="active" ariaCurrentWhenActive="page"
               [routerLinkActiveOptions]="{ exact: !!item.exact }" [attr.aria-label]="item.label"
               [matTooltip]="collapsed() && !isMobile() ? item.label : ''" matTooltipPosition="right" (click)="isMobile() && drawerOpen.set(false)">
              <mat-icon aria-hidden="true">{{ item.icon }}</mat-icon><span class="nav-label">{{ item.label }}</span>
            </a>
          }
        </nav>
        <div class="rail-footer"><mat-icon aria-hidden="true">verified_user</mat-icon><span>{{ userSubtitle() || contextLabel() }}</span></div>
      </mat-sidenav>
      <mat-sidenav-content class="content">
        <header class="topbar">
          <button mat-icon-button type="button" (click)="toggleNavigation()" [attr.aria-label]="isMobile() ? 'Open navigation' : collapsed() ? 'Expand navigation' : 'Collapse navigation'"
                  [attr.aria-expanded]="isMobile() ? drawerOpen() : !collapsed()"><mat-icon>menu</mat-icon></button>
          <div class="context"><span>{{ contextLabel() }}</span><mat-icon aria-hidden="true">chevron_right</mat-icon><strong>{{ currentPage() }}</strong></div>
          <span class="grow"></span><ng-content select="[toolbar]" /><app-theme-toggle />
          <button type="button" class="account" [matMenuTriggerFor]="userMenu" aria-label="Account menu">
            <span class="avatar" aria-hidden="true">{{ initials() }}</span><span class="account-name">{{ userName() }}</span><mat-icon aria-hidden="true">expand_more</mat-icon>
          </button>
          <mat-menu #userMenu="matMenu" xPosition="before">
            <div class="px-4 py-3"><div class="text-sm font-medium">{{ userName() }}</div>
              @if (userSubtitle()) { <div class="mt-1 text-xs text-on-surface-variant">{{ userSubtitle() }}</div> }
            </div><mat-divider />
            @if (profileRoute()) { <a mat-menu-item [routerLink]="profileRoute()"><mat-icon>person</mat-icon>Profile & security</a> }
            <button mat-menu-item type="button" (click)="logout.emit()"><mat-icon>logout</mat-icon>Sign out</button>
          </mat-menu>
        </header>
        <ng-content select="[banner]" />
        <main id="main" tabindex="-1"><router-outlet /></main>
        <ng-content select="[footer]" />
      </mat-sidenav-content>
    </mat-sidenav-container>
  `,
})
export class AppShellComponent {
  readonly appName = input.required<string>();
  readonly logoUrl = input<string | null>(null);
  readonly contextLabel = input<string>('');
  readonly navItems = input.required<NavItem[]>();
  readonly userName = input<string>('');
  readonly userSubtitle = input<string | null>(null);
  readonly profileRoute = input<string | null>(null);
  readonly logout = output<void>();

  private readonly router = inject(Router);
  private readonly url = toSignal(this.router.events.pipe(filter(event => event instanceof NavigationEnd), map(() => this.router.url)), { initialValue: this.router.url });
  readonly currentPage = computed(() => this.navItems().find(item => item.route === this.url().split('?')[0])?.label
    ?? this.navItems().find(item => item.route !== '/' && this.url().startsWith(item.route + '/'))?.label ?? this.contextLabel());
  readonly initials = computed(() => this.userName().split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase());
  readonly collapsed = signal(this.readCollapsed());
  readonly drawerOpen = signal(false);
  toggleNavigation(): void {
    if (this.isMobile()) this.drawerOpen.update(open => !open);
    else {
      this.collapsed.update(value => !value);
      try { localStorage.setItem('esb.shell.collapsed', String(this.collapsed())); } catch { /* session only */ }
    }
  }
  private readCollapsed(): boolean { try { return localStorage.getItem('esb.shell.collapsed') === 'true'; } catch { return false; } }

  readonly isMobile = toSignal(
    inject(BreakpointObserver).observe('(max-width: 1023px)').pipe(map((r) => r.matches)),
    { initialValue: false },
  );
}

/** Two-column branded frame for sign-in and registration pages. */
@Component({
  selector: 'app-auth-layout',
  imports: [ThemeToggleComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './auth-layout.scss',
  template: `
    <div class="auth">
      <aside class="aside" [style.background-image]="backgroundUrl() ? 'url(' + backgroundUrl() + ')' : null">
        @if (backgroundUrl()) { <div class="scrim" aria-hidden="true"></div> }
        <div class="brand">
          @if (logoUrl()) { <img [src]="logoUrl()" alt="" /> } @else { <span class="mark" aria-hidden="true">{{ appName().slice(0, 1) }}</span> }
          <span class="brand-text">{{ appName() }}</span>
        </div>
        <div class="copy"><p class="eyebrow">Your workspace, connected</p><h1>{{ headline() }}</h1>
          @if (subline()) { <p class="subline">{{ subline() }}</p> }<span class="rule" aria-hidden="true"></span>
        </div>
        <p class="footer">{{ footer() }}</p>
      </aside>
      <section class="form-side">
        <div class="preferences"><app-theme-toggle /></div>
        <div class="form-wrap"><div class="form">
          <div class="brand mobile-brand">
            @if (logoUrl()) { <img [src]="logoUrl()" alt="" /> } @else { <span class="mark" aria-hidden="true">{{ appName().slice(0, 1) }}</span> }
            <span class="brand-text">{{ appName() }}</span>
          </div>
          <ng-content />
        </div></div>
      </section>
    </div>
  `,
})
export class AuthLayoutComponent {
  readonly appName = input.required<string>();
  readonly logoUrl = input<string | null>(null);
  readonly backgroundUrl = input<string | null>(null);
  readonly headline = input('Welcome back');
  readonly subline = input<string | null>(null);
  readonly footer = input('');
}

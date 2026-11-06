import { ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, input, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';

import type { NavItem } from '@shared/components';
import type { TenantProfile } from '@shared/models';
import { AuthStore, RealtimeService, ToastService } from '@shared/services';

import { AGENT_PORTAL } from './access';

interface FrameLink {
  label: string;
  icon: string;
  route?: string;
  fragment?: string;
  href?: string;
  exact?: boolean;
}

/** Agent menu shortcuts; each opens its section of the dashboard. */
const GUIDE: FrameLink[] = [
  { label: 'Earnings', icon: 'savings', fragment: 'earnings' },
];

/**
 * Frame of the master agent and agent workspace, built after the SWC MAIN agent reference:
 * a 64px app bar, a 280px temporary drawer under it and full-width content.
 * Other roles keep the shared app shell; this frame is only rendered for agent accounts.
 */
@Component({
  selector: 'app-agent-frame',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, MatIconModule, MatMenuModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './frame.scss',
  host: { '(document:keydown.escape)': 'close()' },
  template: `
    <a class="skip" href="#agent-main">Skip to content</a>
    <header class="bar">
      <button #menuButton type="button" class="nav-icon" (click)="open.set(!open())" aria-label="Open navigation" aria-controls="agent-drawer" [attr.aria-expanded]="open()">
        <mat-icon aria-hidden="true">menu</mat-icon>
      </button>
      <span class="title">{{ brand() }}</span>
      <span class="grow"></span>
      <button type="button" class="user" [matMenuTriggerFor]="account" aria-label="Account menu">
        <mat-icon aria-hidden="true">expand_more</mat-icon>{{ userName() }}
      </button>
      <mat-menu #account="matMenu" xPosition="before" class="agent-menu">
        <div class="who"><strong>{{ userName() }}</strong><span>{{ roleLabel() }}</span></div>
        <a mat-menu-item [routerLink]="profileRoute()"><mat-icon>account_circle</mat-icon>My Account</a>
        <button mat-menu-item type="button" (click)="auth.logout()"><mat-icon>logout</mat-icon>Logout</button>
      </mat-menu>
    </header>

    @if (open()) { <div class="scrim" (click)="close()" aria-hidden="true"></div> }
    <nav id="agent-drawer" class="drawer" [class.open]="open()" aria-label="Agent menu" [attr.inert]="open() ? null : ''">
      <a class="item" [routerLink]="home()" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }" #first>
        <mat-icon aria-hidden="true">home</mat-icon><span>Dashboard</span>
      </a>
      <a class="item" [routerLink]="inboxRoute()" routerLinkActive="active" data-nav="inbox"><mat-icon aria-hidden="true">inbox</mat-icon><span>My Inbox</span></a>
      <a class="item" [routerLink]="usersRoute()" routerLinkActive="active" data-nav="my-users"><mat-icon aria-hidden="true">groups</mat-icon><span>My Users</span></a>
      @if (portal === 'player') {
        <a class="item" routerLink="/arena"><mat-icon aria-hidden="true">live_tv</mat-icon><span>Live Games</span></a>
      } @else {
        <a class="item" href="/"><mat-icon aria-hidden="true">live_tv</mat-icon><span>Live Games</span></a>
      }
      <p class="sub">Agent Menu</p>
      @for (link of guide; track link.fragment) {
        <a class="item" [routerLink]="home()" [fragment]="link.fragment"><mat-icon aria-hidden="true">{{ link.icon }}</mat-icon><span>{{ link.label }}</span></a>
      }
      <a class="item" [routerLink]="commissionRoute()" routerLinkActive="active" data-nav="commission"><mat-icon aria-hidden="true">paid</mat-icon><span>Commission</span></a>
      @if (extra().length) {
        <p class="sub">Agent Tools</p>
        @for (link of extra(); track link.label) {
          @if (link.route) {
            <a class="item" [routerLink]="link.route" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: !!link.exact }"><mat-icon aria-hidden="true">{{ link.icon }}</mat-icon><span>{{ link.label }}</span></a>
          } @else {
            <a class="item" [href]="link.href"><mat-icon aria-hidden="true">{{ link.icon }}</mat-icon><span>{{ link.label }}</span></a>
          }
        }
      }
      <a class="item" [routerLink]="profileRoute()" routerLinkActive="active"><mat-icon aria-hidden="true">account_circle</mat-icon><span>My Account</span></a>
      <button type="button" class="item" (click)="auth.logout()"><mat-icon aria-hidden="true">logout</mat-icon><span>Logout</span></button>
    </nav>

    <main id="agent-main" tabindex="-1"><router-outlet /></main>
  `,
})
export class AgentFrame {
  /** Extra screens of the hosting portal (the admin portal passes its permitted navigation). */
  readonly navItems = input<NavItem[]>([]);
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly portal = inject(AGENT_PORTAL);
  private readonly realtime = inject(RealtimeService);
  private readonly toast = inject(ToastService);
  readonly guide = GUIDE;
  readonly open = signal(false);
  private readonly router = inject(Router);
  private readonly menuButton = viewChild.required<ElementRef<HTMLButtonElement>>('menuButton');
  private readonly first = viewChild.required<ElementRef<HTMLAnchorElement>>('first');

  readonly userName = computed(() => this.auth.user()?.username ?? '');
  readonly roleLabel = computed(() => (this.auth.user()?.account_type === 'master_agent' ? 'Master Agent' : 'Agent'));
  /** Header title: the same for master agents and agents. */
  readonly brand = computed(() => 'SWC MAIN Agent');
  readonly home = computed(() => (this.portal === 'player' ? '/agent' : '/'));
  readonly profileRoute = computed(() => (this.portal === 'player' ? '/agent/account' : '/profile'));
  readonly usersRoute = computed(() => (this.portal === 'player' ? '/agent/users' : '/my-users'));
  readonly commissionRoute = computed(() => (this.portal === 'player' ? '/agent/commission' : '/commission'));
  readonly inboxRoute = computed(() => (this.portal === 'player' ? '/agent/inbox' : '/inbox'));
  readonly extra = computed<FrameLink[]>(() => {
    if (this.portal === 'admin') {
      return this.navItems().filter((item) => !['/', '/my-users', '/commission', '/balance-management'].includes(item.route ?? '')).map((item) => ({ label: item.label, icon: item.icon, route: item.route, exact: item.exact }));
    }
    // The player portal has no extra agent tools: everything lives in the agent workspace itself.
    return [];
  });

  constructor() {
    this.realtime.connect();
    this.realtime.on<{ status?: string; player_username?: string; amount?: string }>('inbox.updated').pipe(takeUntilDestroyed()).subscribe((data) => {
      if (data.status === 'pending' && data.player_username) {
        this.toast.info(`${data.player_username} requested ${data.amount} play points. Accept it in My Inbox.`);
      }
    });
    this.router.events.pipe(filter((event) => event instanceof NavigationEnd), takeUntilDestroyed()).subscribe(() => this.open.set(false));
    let wasOpen = false;
    effect(() => {
      const isOpen = this.open();
      if (isOpen) queueMicrotask(() => this.first().nativeElement.focus());
      else if (wasOpen) this.menuButton().nativeElement.focus();
      wasOpen = isOpen;
    });
  }

  close(): void {
    this.open.set(false);
  }
}

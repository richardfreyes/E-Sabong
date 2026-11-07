import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { RouterLink, RouterOutlet } from '@angular/router';

import { BrandLogoComponent } from '@shared/components';
import type { TenantProfile } from '@shared/models';
import { AuthStore, BrandingService, RealtimeService } from '@shared/services';

import { isStreamOperator } from './guards';

/** Separate, minimal frame for the Operator dashboard (no administration navigation). */
@Component({
  selector: 'app-operator-layout',
  imports: [RouterOutlet, RouterLink, MatButtonModule, MatIconModule, MatMenuModule, BrandLogoComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: flex; flex-direction: column; min-height: 100dvh; background: var(--background); color: var(--text-primary); }
    .bar { position: sticky; top: 0; z-index: 10; display: flex; align-items: center; gap: 12px; padding: 10px clamp(12px, 3vw, 28px);
      background: var(--surface); border-bottom: 1px solid var(--border); }
    .brand { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .brand strong { font-size: .95rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .role { font-size: .66rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; color: var(--accent);
      border: 1px solid currentColor; border-radius: 999px; padding: 2px 9px; white-space: nowrap; }
    .grow { flex: 1; }
    .ws { display: inline-flex; align-items: center; gap: 6px; font-size: .72rem; color: var(--warning-text); white-space: nowrap; }
    .ws i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
    .ws.open { color: var(--success-text); }
    .who { font-size: .82rem; color: var(--text-secondary); white-space: nowrap; }
    main { flex: 1; width: 100%; max-width: 1480px; margin: 0 auto; padding: clamp(12px, 2.4vw, 24px); }
    @media (max-width: 600px) { .who, .ws span { display: none; } }
  `,
  template: `
    <header class="bar">
      <div class="brand">
        <app-brand-logo [size]="30" [alt]="branding.appName()" />
        <strong>{{ branding.appName() }}</strong>
        <span class="role">Stream Operator</span>
      </div>
      <span class="grow"></span>
      <span class="ws" [class.open]="realtime.state() === 'open'" role="status"
        [attr.aria-label]="realtime.state() === 'open' ? 'Live updates connected' : 'Live updates reconnecting'">
        <i aria-hidden="true"></i><span>{{ realtime.state() === 'open' ? 'Live updates' : 'Reconnecting…' }}</span>
      </span>
      @if (!operatorOnly()) { <a mat-button routerLink="/">Administration</a> }
      <span class="who">{{ auth.user()?.full_name }}</span>
      <button mat-icon-button type="button" [matMenuTriggerFor]="menu" aria-label="Account menu"><mat-icon>account_circle</mat-icon></button>
      <mat-menu #menu="matMenu">
        <a mat-menu-item routerLink="/change-password"><mat-icon>key</mat-icon>Change password</a>
        <button mat-menu-item type="button" (click)="auth.logout()"><mat-icon>logout</mat-icon>Sign out</button>
      </mat-menu>
    </header>
    <main><router-outlet /></main>
  `,
})
export class OperatorLayout {
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly branding = inject(BrandingService);
  readonly realtime = inject(RealtimeService);
  readonly operatorOnly = computed(() => isStreamOperator(this.auth.user()));

  constructor() {
    this.realtime.connect();
  }
}

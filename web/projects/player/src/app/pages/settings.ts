import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { RouterLink } from '@angular/router';

import { BrandingService } from '@shared/services';
import { PlayerPrefs } from '../core/player-prefs';
import { ConnectionStatusComponent } from '../ui/connection-status';
import { PageHeadComponent } from '../ui/page-head';

/** Display preferences saved on this device. Account details live in Profile. */
@Component({
  selector: 'app-player-settings',
  imports: [RouterLink, MatSlideToggleModule, MatIconModule, PageHeadComponent, ConnectionStatusComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .stack { display: flex; flex-direction: column; gap: 1rem; max-width: 46rem; }
    .row { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 0.9rem 1.1rem; }
    .row + .row { border-top: 1px solid var(--pp-rope); }
    .row p { margin: 0; }
    .name { color: var(--pp-chalk); font-weight: 500; }
    .hint { color: var(--pp-ash); font-size: 0.85rem; margin-top: 0.15rem !important; max-width: 52ch; }
    .links a { display: flex; align-items: center; gap: 0.75rem; padding: 0.85rem 1.1rem; color: var(--pp-chalk); text-decoration: none; }
    .links a + a { border-top: 1px solid var(--pp-rope); }
    .links a:hover { background: var(--pp-pit-raised); }
    .links mat-icon:last-child { margin-left: auto; color: var(--pp-ash); }
    @media (max-width: 539px) { .row.wrap { flex-direction: column; align-items: flex-start; } }
  `,
  template: `
    <pp-page-head title="Settings" subtitle="These preferences are saved on this device." />
    <div class="stack">
      <section class="pp-panel" aria-labelledby="live-title">
        <div class="pp-panel-head"><h2 id="live-title" class="pp-h2">Live updates</h2><pp-connection-status /></div>
        <div class="row">
          <div><p class="name">Update results as they happen</p>
            <p class="hint">Keeps a live connection for fight status, official results and points. Turn off to save data; pages then refresh when you open them.</p></div>
          <mat-slide-toggle [checked]="prefs.liveUpdates()" (change)="prefs.update({ liveUpdates: $event.checked })" aria-label="Update results as they happen"
                            [disabled]="liveDisabledByWorkspace()" />
        </div>
        @if (liveDisabledByWorkspace()) { <p class="hint px-[1.1rem] pb-4 m-0">Your organiser has turned live updates off for this workspace.</p> }
        <div class="row">
          <div><p class="name">Result announcements</p><p class="hint">Show a banner across the arena when an official result is declared.</p></div>
          <mat-slide-toggle [checked]="prefs.announcements()" (change)="prefs.update({ announcements: $event.checked })" aria-label="Result announcements" />
        </div>
      </section>

      <section class="pp-panel" aria-labelledby="video-title">
        <div class="pp-panel-head"><h2 id="video-title" class="pp-h2">Arena video</h2></div>
        <div class="row">
          <div><p class="name">Start muted</p><p class="hint">Browsers only start a video on their own when it is silent.</p></div>
          <mat-slide-toggle [checked]="prefs.videoMuted()" (change)="prefs.update({ videoMuted: $event.checked })" aria-label="Start muted" />
        </div>
        <div class="row"><p class="hint">Big Buck Bunny · Blender Foundation · <a href="https://creativecommons.org/licenses/by/3.0/" target="_blank" rel="noopener noreferrer">CC BY 3.0</a></p></div>
      </section>

      <section class="pp-panel" aria-labelledby="display-title">
        <div class="pp-panel-head"><h2 id="display-title" class="pp-h2">Display</h2></div>
        <div class="row">
          <div><p class="name">Reduce motion</p><p class="hint">Turns off pulsing indicators and result animations.</p></div>
          <mat-slide-toggle [checked]="prefs.reduceMotion()" (change)="prefs.update({ reduceMotion: $event.checked })" aria-label="Reduce motion" />
        </div>
        <div class="row">
          <div><p class="name">Compact side menu</p><p class="hint">Show only icons in the side menu on larger screens.</p></div>
          <mat-slide-toggle [checked]="prefs.railCollapsed()" (change)="prefs.update({ railCollapsed: $event.checked })" aria-label="Compact side menu" />
        </div>
      </section>

      <section class="pp-panel links" aria-labelledby="account-title">
        <div class="pp-panel-head"><h2 id="account-title" class="pp-h2">Account</h2></div>
        <a routerLink="/profile"><mat-icon aria-hidden="true">person</mat-icon>Profile and password<mat-icon aria-hidden="true">chevron_right</mat-icon></a>
        <a routerLink="/activity"><mat-icon aria-hidden="true">history</mat-icon>Account activity<mat-icon aria-hidden="true">chevron_right</mat-icon></a>
      </section>

      <p class="hint m-0">Demo points have no cash value and cannot be redeemed.</p>
    </div>
  `,
})
export class SettingsPage {
  readonly prefs = inject(PlayerPrefs);
  private readonly branding = inject(BrandingService);
  readonly liveDisabledByWorkspace = () => this.branding.config()?.feature_flags?.['live_updates'] === false;
}

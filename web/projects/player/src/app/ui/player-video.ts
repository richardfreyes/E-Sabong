import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import type { EventItem, Match } from '@shared/models';
import type { Announcement } from '../core/arena.store';
import { toCents } from '@shared/pipes';
import { MIN_STREAM_CENTS } from '../core/play-betting';
import { VideoStageComponent } from './video-stage';

export type StreamAccess = 'checking' | 'allowed' | 'denied' | 'error';

/**
 * Live-video access from the available balance plus open fight stakes (`balance + stakes >= 100`).
 * A bet moves points from the balance into an open stake, so betting never ends access; settling
 * does when nothing is left. The server enforces the same rule on every playback and media session.
 */
export function streamAccess(balance: string | null, openStakes: string | null, failed: boolean): StreamAccess {
  const funds = balance === null ? null : toCents(balance);
  const stakes = openStakes === null ? null : toCents(openStakes);
  if (funds !== null && stakes !== null) return funds + stakes >= MIN_STREAM_CENTS ? 'allowed' : 'denied';
  // Stakes only ever add access: a sufficient balance alone is enough to start.
  if (funds !== null && funds >= MIN_STREAM_CENTS) return 'allowed';
  return failed ? 'error' : 'checking';
}

/** Player-only gate: destroying the stage closes transports and cancels reconnects immediately. */
@Component({
  selector: 'pp-player-video',
  imports: [VideoStageComponent, MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: flex; flex-direction: column; }
    pp-video-stage { display: flex; flex-direction: column; }
    .screen { aspect-ratio: 16 / 9; display: grid; place-items: center; background: #000; }
    .balance-message { padding: 16px; max-width: 440px; text-align: center; color: #fff; }
    .balance-message mat-icon { color: #f3c94c; }
    .balance-message button { margin-top: 12px; padding: 6px 16px; border: 1px solid #f3c94c; border-radius: 4px; background: transparent; color: #f3c94c; font: inherit; cursor: pointer; }
    h3 { margin: 8px 0; font-size: clamp(16px, 2vw, 22px); }
    p { margin: 0; color: #ccc; font-size: clamp(12px, 1.5vw, 14px); line-height: 1.5; }
    .foot { min-height: 36px; background: var(--panel-deep); }
  `,
  template: `
    @if (access() === 'allowed') {
      <pp-video-stage [globalPlayback]="true" [requireMatch]="false" [event]="event()" [match]="match()" [announcement]="announcement()"
        [loading]="loading()" [storeError]="storeError()" (refresh)="refresh.emit()" (dismiss)="dismiss.emit()" />
    } @else {
      <section class="frame" aria-label="Live video access">
        <div class="screen">
          <div class="balance-message" role="status" aria-live="polite">
            @switch (access()) {
              @case ('denied') {
                <mat-icon aria-hidden="true">lock</mat-icon>
                <h3>Insufficient Balance</h3>
                <p>Your balance and active bets must total at least 100 points to watch the live stream. Please contact your Agent to add balance.</p>
              }
              @case ('error') {
                <mat-icon aria-hidden="true">wifi_off</mat-icon>
                <h3>Couldn't Check Live Stream Access</h3>
                <p>Check your connection and try again.</p>
                <button type="button" (click)="retry.emit()">Try again</button>
              }
              @default {
                <mat-icon aria-hidden="true">hourglass_empty</mat-icon>
                <h3>Checking Live Stream Access</h3>
                <p>Verifying your balance and active bets.</p>
              }
            }
          </div>
        </div>
        <div class="foot" aria-hidden="true"></div>
      </section>
    }
  `,
})
export class PlayerVideoComponent {
  readonly balance = input<string | null>(null);
  readonly openStakes = input<string | null>(null);
  /** The wallet could not be read; shows a retry instead of an endless check. */
  readonly accessError = input(false);
  readonly event = input<EventItem | null>(null);
  readonly match = input<Match | null>(null);
  readonly announcement = input<Announcement | null>(null);
  readonly loading = input(false);
  readonly storeError = input<string | null>(null);
  readonly refresh = output<void>();
  readonly dismiss = output<void>();
  readonly retry = output<void>();
  readonly access = computed(() => streamAccess(this.balance(), this.openStakes(), this.accessError()));
}

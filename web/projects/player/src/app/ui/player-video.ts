import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import type { EventItem, Match } from '@shared/models';
import type { Announcement } from '../core/arena.store';
import { toCents } from '@shared/pipes';
import { VideoStageComponent } from './video-stage';

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
    h3 { margin: 8px 0; font-size: clamp(16px, 2vw, 22px); }
    p { margin: 0; color: #ccc; font-size: clamp(12px, 1.5vw, 14px); line-height: 1.5; }
    .foot { min-height: 36px; background: var(--panel-deep); }
  `,
  template: `
    @if (canWatch()) {
      <pp-video-stage [event]="event()" [match]="match()" [announcement]="announcement()"
        [loading]="loading()" [storeError]="storeError()" (refresh)="refresh.emit()" (dismiss)="dismiss.emit()" />
    } @else {
      <section class="frame" aria-label="Live video access">
        <div class="screen">
          <div class="balance-message" role="status" aria-live="polite">
            <mat-icon aria-hidden="true">{{ balance() === null ? 'hourglass_empty' : 'lock' }}</mat-icon>
            <h3>{{ balance() === null ? 'Checking Balance' : 'Insufficient Balance' }}</h3>
            <p>{{ balance() === null ? 'Checking your live stream access.' : 'Your balance must be at least 100 points to watch the live stream. Please contact your Agent to add balance.' }}</p>
          </div>
        </div>
        <div class="foot" aria-hidden="true"></div>
      </section>
    }
  `,
})
export class PlayerVideoComponent {
  readonly balance = input<string | null>(null);
  readonly event = input<EventItem | null>(null);
  readonly match = input<Match | null>(null);
  readonly announcement = input<Announcement | null>(null);
  readonly loading = input(false);
  readonly storeError = input<string | null>(null);
  readonly refresh = output<void>();
  readonly dismiss = output<void>();
  readonly canWatch = computed(() => (toCents(this.balance() ?? '') ?? -1) >= 10000);
}

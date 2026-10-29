import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';

import type { EventItem, Match } from '@shared/models';
import { RealtimeService } from '@shared/services';
import type { Announcement } from './models';
import { OUTCOME_TEXT, matchPhase } from './match-phase';
import { PlayerPrefs } from './player-prefs';
import { PhasePillComponent } from './phase-pill';

/** The bundled, openly licensed clip the display falls back to. */
export const DEMO_CLIP = '/media/demo-loop.mp4';

type VideoState = 'loading' | 'ready' | 'blocked' | 'error' | 'offline';

/**
 * The 16:9 arena display: the largest element on the page.
 *
 * Uses the bundled, openly licensed demonstration clip. No broadcast source is
 * accepted. Loading and recovery states keep the current fight visible.
 */
@Component({
  selector: 'pp-video-stage',
  imports: [MatButtonModule, MatIconModule, MatTooltipModule, PhasePillComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(document:fullscreenchange)': 'syncFullscreen()' },
  styleUrl: './video-stage.scss',
  template: `
    <section #frame class="frame" [class.is-fullscreen]="fullscreen()">
      @if (storeError() && match()) {
        <div class="connection-error" role="alert"><mat-icon aria-hidden="true">cloud_off</mat-icon>Match updates unavailable
          <button mat-button type="button" (click)="refresh.emit()">Retry</button>
        </div>
      }
      <div class="screen" role="region" [attr.aria-label]="screenLabel()">
        <video #video playsinline loop preload="metadata" [attr.aria-label]="videoLabel()"
               (loadeddata)="onReady()" (playing)="onReady()" (pause)="playing.set(false)" (waiting)="onWaiting()" (error)="onError()"></video>

        @if (!showVideo()) {
          <div class="placeholder">
            <div class="state">
              <mat-icon aria-hidden="true">{{ stateIcon() }}</mat-icon>
              <p class="state-title">{{ stateTitle() }}</p>
              <p class="state-text">{{ stateText() }}</p>
              @if (!loading()) {
                <div class="state-actions">
                  <button mat-stroked-button type="button" (click)="retry()">
                    <mat-icon>{{ state() === 'blocked' ? 'play_arrow' : 'refresh' }}</mat-icon>{{ state() === 'blocked' ? 'Play' : 'Try again' }}
                  </button>
                </div>
              }
            </div>
          </div>
        }

        <div class="over over-top">
          @if (match(); as m) {
            <span class="fight-badge"><span class="word">Fight</span><span class="pp-num no">{{ m.match_number }}</span></span>
            <pp-phase-pill [phase]="phaseOf(m)" />
          }
          <span class="grow"></span>
          <span class="source-badge" [matTooltip]="sourceTooltip()">
            <mat-icon aria-hidden="true">movie</mat-icon>{{ sourceLabel() }}
          </span>
        </div>

        @if (announcement(); as a) {
          <div class="announce" [class]="a.outcome" role="alert">
            <div class="announce-band">
              <span class="announce-kicker">{{ a.corrected ? 'Result corrected' : 'Official result' }}, fight {{ a.matchNumber }}</span>
              <span class="announce-text pp-display">{{ outcomeText(a.outcome) }}</span>
            </div>
            <button mat-icon-button type="button" class="announce-close" (click)="dismiss.emit()" aria-label="Dismiss announcement">
              <mat-icon>close</mat-icon>
            </button>
          </div>
        }
      </div>

      @if (fullscreenError()) { <p class="fullscreen-error" role="alert">{{ fullscreenError() }}</p> }
      <footer class="foot">
        <span class="feed" [class]="feedState()">
          <mat-icon aria-hidden="true">sensors</mat-icon>
          <span>Live updates</span>
          <span class="feed-state"><span class="feed-word">{{ feedLabel() }}</span></span>
        </span>
        <span class="grow"></span>
        @if (showVideo()) {
          <button mat-icon-button type="button" (click)="togglePlay()" [matTooltip]="playing() ? 'Pause' : 'Play'"
                  [attr.aria-label]="playing() ? 'Pause video' : 'Play video'">
            <mat-icon>{{ playing() ? 'pause' : 'play_arrow' }}</mat-icon>
          </button>
          <button mat-icon-button type="button" (click)="toggleMute()" [matTooltip]="muted() ? 'Unmute' : 'Mute'"
                  [attr.aria-label]="muted() ? 'Unmute video' : 'Mute video'">
            <mat-icon>{{ muted() ? 'volume_off' : 'volume_up' }}</mat-icon>
          </button>
        }
        <button mat-button type="button" (click)="reload()" matTooltip="Reload the video and reconnect live updates">
          <mat-icon>refresh</mat-icon>Refresh
        </button>
        <button mat-icon-button type="button" (click)="toggleFullscreen()" [disabled]="!canFullscreen"
                [matTooltip]="fullscreen() ? 'Exit full screen' : 'Full screen'"
                [attr.aria-label]="fullscreen() ? 'Exit full screen' : 'Full screen'">
          <mat-icon>{{ fullscreen() ? 'fullscreen_exit' : 'fullscreen' }}</mat-icon>
        </button>
      </footer>
    </section>
  `,
})
export class VideoStageComponent {
  readonly forceLiveUpdates = input(false);
  readonly liveUpdatesAllowed = input(true);
  readonly event = input<EventItem | null>(null);
  readonly match = input<Match | null>(null);
  readonly announcement = input<Announcement | null>(null);
  readonly loading = input(false);
  readonly storeError = input<string | null>(null);
  readonly refresh = output<void>();
  readonly dismiss = output<void>();

  private readonly frame = viewChild.required<ElementRef<HTMLElement>>('frame');
  private readonly video = viewChild.required<ElementRef<HTMLVideoElement>>('video');
  private readonly realtime = inject(RealtimeService);
  private readonly prefs = inject(PlayerPrefs);
  readonly fullscreenError = signal('');

  readonly fullscreen = signal(false);
  readonly playing = signal(false);
  readonly muted = signal(this.prefs.videoMuted());
  readonly state = signal<VideoState>('loading');
  readonly online = signal(typeof navigator === 'undefined' || navigator.onLine);
  readonly canFullscreen = typeof document !== 'undefined' && !!document.fullscreenEnabled;

  readonly phaseOf = matchPhase;
  readonly outcomeText = (o: keyof typeof OUTCOME_TEXT) => OUTCOME_TEXT[o];

  /** Only the bundled demonstration source is accepted. */
  readonly source = () => new URL('media/demo-loop.mp4', document.baseURI).pathname;
  readonly sourceLabel = () => 'Demo video';
  readonly sourceTooltip = () => 'Big Buck Bunny · Blender Foundation · CC BY 3.0. Authorized demonstration video.';

  /** A missing fight card is reported instead of the video's own state. */
  private readonly cardIssue = computed<'loading' | 'error' | 'no-event' | 'no-fights' | null>(() => {
    if (this.match()) return null;
    if (this.loading()) return 'loading';
    if (this.storeError()) return 'error';
    return this.event() ? 'no-fights' : 'no-event';
  });

  readonly showVideo = computed(() => this.state() === 'ready' && !this.cardIssue());
  readonly videoLabel = computed(() => {
    const m = this.match();
    return m ? `Demonstration video shown alongside fight ${m.match_number}` : 'Demonstration video';
  });

  readonly stateIcon = computed(() => {
    switch (this.cardIssue()) {
      case 'error':
        return 'cloud_off';
      case 'no-event':
        return 'event_busy';
      case 'no-fights':
        return 'format_list_numbered';
      case 'loading':
        return 'hourglass_empty';
      default:
        break;
    }
    switch (this.state()) {
      case 'offline':
        return 'wifi_off';
      case 'error':
        return 'videocam_off';
      case 'blocked':
        return 'play_circle';
      default:
        return 'hourglass_empty';
    }
  });

  readonly stateTitle = computed(() => {
    switch (this.cardIssue()) {
      case 'error':
        return "The fight card didn't load";
      case 'no-event':
        return 'No events on the calendar';
      case 'no-fights':
        return 'Fight card not published yet';
      case 'loading':
        return 'Loading the fight card';
      default:
        break;
    }
    switch (this.state()) {
      case 'offline':
        return "You're offline";
      case 'error':
        return "The video didn't load";
      case 'blocked':
        return 'Ready to play';
      default:
        return 'Loading the video';
    }
  });

  readonly stateText = computed(() => {
    switch (this.cardIssue()) {
      case 'error':
        return 'Match information is temporarily unavailable. Please try again.';
      case 'no-event':
        return 'Live and upcoming derbies appear here as soon as they are published.';
      case 'no-fights':
        return 'Fights appear here once the organiser adds them to the card.';
      case 'loading':
        return 'Fetching the fights for this event.';
      default:
        break;
    }
    switch (this.state()) {
      case 'offline':
        return 'Results still arrive as soon as the connection is back.';
      case 'error':
        return 'Check your connection and try again.';
      case 'blocked':
        return 'Your browser asked for a tap before starting playback.';
      default:
        return 'The fight number and official result do not depend on the video.';
    }
  });

  readonly feedState = computed(() => !this.liveUpdatesAllowed() ? 'paused' : ((this.forceLiveUpdates() || this.prefs.liveUpdates()) ? this.realtime.state() : 'paused'));
  readonly feedLabel = computed(() => {
    switch (this.feedState()) {
      case 'open':
        return 'Updating live';
      case 'connecting':
        return 'Reconnecting';
      case 'paused':
        return 'Live updates off';
      default:
        return 'Offline';
    }
  });
  readonly screenLabel = computed(() => {
    const m = this.match();
    if (!m) return 'Arena display';
    const state = m.result ? OUTCOME_TEXT[m.result.outcome] : matchPhase(m).label;
    return `Arena display: fight ${m.match_number}, MERON versus WALA, ${state}`;
  });

  constructor() {
    effect(() => {
      const src = this.source();
      const el = this.video().nativeElement;
      if (el.getAttribute('src') === src) return;
      el.setAttribute('src', src);
      this.state.set(this.online() ? 'loading' : 'offline');
      el.load();
      void this.start();
    });

    const online = () => {
      this.online.set(navigator.onLine);
      if (navigator.onLine && this.state() === 'offline') this.reload();
      else if (!navigator.onLine) this.state.set('offline');
    };
    addEventListener('online', online);
    addEventListener('offline', online);
    inject(DestroyRef).onDestroy(() => {
      removeEventListener('online', online);
      removeEventListener('offline', online);
    });
  }

  onReady(): void {
    this.state.set(this.online() ? 'ready' : 'offline');
    this.playing.set(!this.video().nativeElement.paused);
  }

  onWaiting(): void {
    this.state.set(this.online() ? 'loading' : 'offline');
  }

  onError(): void {
    this.state.set(this.online() ? 'error' : 'offline');
    this.playing.set(false);
  }

  retry(): void {
    if (this.state() === 'blocked') void this.start();
    else this.reload();
  }

  reload(): void {
    const el = this.video().nativeElement;
    this.state.set(this.online() ? 'loading' : 'offline');
    el.load();
    void this.start();
    this.refresh.emit();
  }

  togglePlay(): void {
    const el = this.video().nativeElement;
    if (el.paused) void this.start();
    else {
      el.pause();
      this.playing.set(false);
    }
  }

  toggleMute(): void {
    this.muted.update((m) => !m);
    this.video().nativeElement.muted = this.muted();
    this.prefs.update({ videoMuted: this.muted() });
  }

  async toggleFullscreen(): Promise<void> {
    this.fullscreenError.set('');
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await this.frame().nativeElement.requestFullscreen();
    } catch {
      this.fullscreenError.set('Full screen is unavailable. Try again from the video controls.');
    }
  }

  syncFullscreen(): void {
    this.fullscreen.set(document.fullscreenElement === this.frame().nativeElement);
  }

  /** Autoplay is refused unless muted, so a refusal becomes a visible play button. */
  private async start(): Promise<void> {
    const el = this.video().nativeElement;
    el.muted = this.muted();
    try {
      await el.play();
      this.playing.set(true);
    } catch {
      this.playing.set(false);
      if (this.state() !== 'error' && this.state() !== 'offline') this.state.set('blocked');
    }
  }
}

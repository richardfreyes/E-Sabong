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
  untracked,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';

import type { EventItem, Match } from '@shared/models';
import { ApiClient, RealtimeService } from '@shared/services';
import { Subscription } from 'rxjs';
import type Hls from 'hls.js';
import type { MediaPlayerClass } from 'dashjs';
import type { Announcement } from './models';
import { OUTCOME_TEXT, matchPhase } from './match-phase';
import { PlayerPrefs } from './player-prefs';
import { PhasePillComponent } from './phase-pill';

/** The bundled, openly licensed clip the display falls back to. */
export const DEMO_CLIP = '/media/demo-loop.mp4';

export interface PlaybackSource {
  type: 'mp4' | 'hls' | 'dash';
  url: string;
  expires_at?: string | null;
}
interface VideoResponse {
  status: string;
  message: string;
  playback?: PlaybackSource | null;
  metadata?: Record<string, unknown> | null;
}

type VideoState = 'loading' | 'ready' | 'blocked' | 'error' | 'offline';

/**
 * The 16:9 arena display: the largest element on the page.
 *
 * Loads backend-authorized playback into the existing arena container.
 * Loading and recovery states keep the current fight visible.
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
        <div class="connection-error" role="alert">
          <mat-icon aria-hidden="true">cloud_off</mat-icon>Match updates unavailable
          <button mat-button type="button" (click)="refresh.emit()">Retry</button>
        </div>
      }
      <div class="screen" role="region" [attr.aria-label]="screenLabel()">
        <video
          #video
          playsinline
          [loop]="sourceLabel() === 'Demo video'"
          preload="metadata"
          [attr.aria-label]="videoLabel()"
          (loadeddata)="onReady()"
          (playing)="onReady()"
          (pause)="playing.set(false)"
          (waiting)="onWaiting()"
          (error)="onError()"
        ></video>

        @if (!showVideo()) {
          <div class="placeholder">
            <div class="state">
              <mat-icon aria-hidden="true">{{ stateIcon() }}</mat-icon>
              <p class="state-title" role="status">{{ providerMessage() || stateTitle() }}</p>
              <p class="state-text">{{ stateText() }}</p>
              @if (!loading()) {
                <div class="state-actions">
                  <button mat-stroked-button type="button" (click)="retry()">
                    <mat-icon>{{ state() === 'blocked' ? 'play_arrow' : 'refresh' }}</mat-icon
                    >{{ state() === 'blocked' ? 'Play' : 'Try again' }}
                  </button>
                </div>
              }
            </div>
          </div>
        }

        <div class="over over-top">
          @if (match(); as m) {
            <span class="fight-badge"
              ><span class="word">Fight</span
              ><span class="pp-num no">{{ m.match_number }}</span></span
            >
            <pp-phase-pill [phase]="phaseOf(m)" />
          }
          <span class="grow"></span>
          <span class="source-badge" [matTooltip]="sourceTooltip()">
            <mat-icon aria-hidden="true">movie</mat-icon><span class="source-word">{{ sourceLabel() }}</span>
          </span>
        </div>

        @if (announcement(); as a) {
          <div class="announce" [class]="a.outcome" role="alert">
            <div class="announce-band">
              <span class="announce-kicker"
                >{{ a.corrected ? 'Result corrected' : 'Official result' }}, fight
                {{ a.matchNumber }}</span
              >
              <span class="announce-text pp-display">{{ outcomeText(a.outcome) }}</span>
            </div>
            <button
              mat-icon-button
              type="button"
              class="announce-close"
              (click)="dismiss.emit()"
              aria-label="Dismiss announcement"
            >
              <mat-icon>close</mat-icon>
            </button>
          </div>
        }
      </div>

      @if (externalMetadata()) {
        <p role="status">{{ externalMetadata() }}</p>
      }
      @if (fullscreenError()) {
        <p class="fullscreen-error" role="alert">{{ fullscreenError() }}</p>
      }
      <footer class="foot">
        <span class="feed" [class]="feedState()">
          <mat-icon aria-hidden="true">sensors</mat-icon>
          <span>Live updates</span>
          <span class="feed-state"
            ><span class="feed-word">{{ feedLabel() }}</span></span
          >
        </span>
        <span class="grow"></span>
        @if (showVideo()) {
          <button
            mat-icon-button
            type="button"
            (click)="togglePlay()"
            [matTooltip]="playing() ? 'Pause' : 'Play'"
            [attr.aria-label]="playing() ? 'Pause video' : 'Play video'"
          >
            <mat-icon>{{ playing() ? 'pause' : 'play_arrow' }}</mat-icon>
          </button>
          <button
            mat-icon-button
            type="button"
            (click)="toggleMute()"
            [matTooltip]="muted() ? 'Unmute' : 'Mute'"
            [attr.aria-label]="muted() ? 'Unmute video' : 'Mute video'"
          >
            <mat-icon>{{ muted() ? 'volume_off' : 'volume_up' }}</mat-icon>
          </button>
        }
        <button
          mat-button
          class="refresh-source"
          type="button"
          (click)="reload()"
          [disabled]="fetching()"
          matTooltip="Fetch authorized playback again"
        >
          <mat-icon>refresh</mat-icon><span class="control-label">Refresh Video</span>
        </button>
        @if (audience() === 'declarator') {
          <button mat-button type="button" (click)="fetchSource('event')" [disabled]="fetching()">
            Refetch Event
          </button>
          <button
            mat-button
            type="button"
            (click)="fetchSource('reconnect')"
            [disabled]="fetching()"
          >
            Reconnect
          </button>
        }
        <span class="provider-status" role="status">{{
          fetching() ? 'Connecting to video source' : providerStatus()
        }}</span>
        <button
          mat-icon-button
          type="button"
          (click)="toggleFullscreen()"
          [disabled]="!canFullscreen"
          [matTooltip]="fullscreen() ? 'Exit full screen' : 'Full screen'"
          [attr.aria-label]="fullscreen() ? 'Exit full screen' : 'Full screen'"
        >
          <mat-icon>{{ fullscreen() ? 'fullscreen_exit' : 'fullscreen' }}</mat-icon>
        </button>
      </footer>
    </section>
  `,
})
export class VideoStageComponent {
  readonly playbackSource = input<PlaybackSource | null | undefined>(undefined);
  readonly audience = input<'player' | 'declarator'>('player');
  private readonly api = inject(ApiClient);
  private request?: Subscription;
  private hls?: Hls;
  private dash?: MediaPlayerClass;
  private generation = 0;
  private expiry?: ReturnType<typeof setTimeout>;
  private loadTimeout?: ReturnType<typeof setTimeout>;
  private destroyed = false;
  readonly fetching = signal(false);
  readonly providerMessage = signal('');
  readonly providerStatus = signal('');
  readonly externalMetadata = signal('');
  private readonly receivedSource = signal<PlaybackSource | null>(null);
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

  private readonly eventId = computed(() => this.event()?.id ?? null);
  readonly source = computed(() =>
    this.playbackSource() === undefined ? this.receivedSource() : this.playbackSource(),
    { equal: (a, b) => a?.url === b?.url && a?.type === b?.type && a?.expires_at === b?.expires_at },
  );
  readonly sourceLabel = computed(() =>
    this.source()?.url === '/media/demo-loop.mp4' ? 'Demo video' : 'External video',
  );
  readonly sourceTooltip = () =>
    this.sourceLabel() === 'Demo video'
      ? 'Big Buck Bunny · Blender Foundation · CC BY 3.0'
      : 'Authorized provider source';

  /** A missing fight card is reported instead of the video's own state. */
  private readonly cardIssue = computed<'loading' | 'error' | 'no-event' | 'no-fights' | null>(
    () => {
      if (this.match()) return null;
      if (this.loading()) return 'loading';
      if (this.storeError()) return 'error';
      return this.event() ? 'no-fights' : 'no-event';
    },
  );

  readonly showVideo = computed(() => this.state() === 'ready' && !this.cardIssue());
  readonly videoLabel = computed(() => {
    const m = this.match();
    return m ? `${this.sourceLabel()} shown alongside fight ${m.match_number}` : this.sourceLabel();
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

  readonly feedState = computed(() =>
    !this.liveUpdatesAllowed()
      ? 'paused'
      : this.forceLiveUpdates() || this.prefs.liveUpdates()
        ? this.realtime.state()
        : 'paused',
  );
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
      // Keyed on the id, not the event object: refetches and realtime frames hand over new
      // objects for the same event, and those must never reconnect the stream.
      const eventId = this.eventId();
      const audience = this.audience();
      if (this.playbackSource() !== undefined) return;
      untracked(() => {
        this.request?.unsubscribe();
        this.fetching.set(false);
        this.receivedSource.set(null);
        this.externalMetadata.set('');
        if (eventId) this.fetchSource('playback', eventId, audience);
        else {
          this.cleanup();
          this.providerMessage.set('Select an event to load video');
        }
      });
    });
    effect(() => {
      const source = this.source();
      untracked(() => {
        void this.initialize(source ?? null);
      });
    });
    const changes = this.realtime.on('video.source_changed').subscribe(() => {
      this.request?.unsubscribe();
      this.fetching.set(false);
      this.fetchSource('playback');
    });

    const online = () => {
      this.online.set(navigator.onLine);
      if (navigator.onLine && this.state() === 'offline') this.reload();
      else if (!navigator.onLine) this.state.set('offline');
    };
    addEventListener('online', online);
    addEventListener('offline', online);
    inject(DestroyRef).onDestroy(() => {
      this.destroyed = true;
      changes.unsubscribe();
      this.request?.unsubscribe();
      this.cleanup();
      removeEventListener('online', online);
      removeEventListener('offline', online);
    });
  }

  onReady(): void {
    if (!this.source() || this.providerMessage()) return;
    clearTimeout(this.loadTimeout);
    this.providerStatus.set(
      this.sourceLabel() === 'Demo video' ? 'Demo playback ready' : 'Playback ready',
    );
    this.state.set(this.online() ? 'ready' : 'offline');
    this.playing.set(!this.video().nativeElement.paused);
  }

  onWaiting(): void {
    clearTimeout(this.loadTimeout);
    this.loadTimeout = setTimeout(() => this.onError(), 25000);
    this.state.set(this.online() ? 'loading' : 'offline');
  }

  onError(): void {
    clearTimeout(this.loadTimeout);
    this.providerStatus.set('Playback error');
    this.state.set(this.online() ? 'error' : 'offline');
    this.playing.set(false);
  }

  retry(): void {
    if (this.state() === 'blocked') void this.start();
    else this.reload();
  }

  reload(): void {
    if (this.playbackSource() !== undefined) void this.initialize(this.source() ?? null);
    else this.fetchSource('refresh');
    this.refresh.emit();
  }

  fetchSource(
    action: 'playback' | 'refresh' | 'reconnect' | 'event',
    eventId = this.event()?.id,
    audience = this.audience(),
  ): void {
    if (!eventId || this.fetching() || this.destroyed) return;
    this.fetching.set(true);
    if (action !== 'event') {
      this.cleanup();
      this.receivedSource.set(null);
      this.state.set('loading');
      this.providerMessage.set('Connecting to video source');
    }
    this.request = this.api
      .post<VideoResponse>(`/video/events/${eventId}/${action}?audience=${audience}`)
      .subscribe({
        next: (result) => {
          this.fetching.set(false);
          if (action === 'event') {
            this.externalMetadata.set(
              result.metadata
                ? Object.entries(result.metadata)
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(' · ')
                : result.message,
            );
            return;
          }
          this.providerStatus.set(result.status);
          this.providerMessage.set(result.playback ? '' : result.message);
          this.receivedSource.set(result.playback ?? null);
          if (!result.playback) this.state.set('error');
        },
        error: () => {
          this.fetching.set(false);
          if (action === 'event')
            this.externalMetadata.set('External event metadata could not be retrieved');
          else {
            this.providerMessage.set('Video temporarily unavailable');
            this.state.set('error');
          }
        },
      });
  }

  private cleanup(): void {
    this.generation++;
    clearTimeout(this.expiry);
    clearTimeout(this.loadTimeout);
    this.hls?.destroy();
    this.hls = undefined;
    this.dash?.reset();
    this.dash = undefined;
    const el = this.video().nativeElement;
    el.pause();
    el.removeAttribute('src');
    el.load();
    this.playing.set(false);
  }

  private async initialize(source: PlaybackSource | null): Promise<void> {
    this.cleanup();
    if (!source || this.destroyed) return;
    const generation = this.generation;
    this.providerMessage.set('');
    this.state.set('loading');
    const el = this.video().nativeElement;
    const expires = source.expires_at ? Date.parse(source.expires_at) - Date.now() : null;
    if (expires !== null) {
      if (!Number.isFinite(expires) || expires <= 0) {
        this.providerMessage.set('Provider authentication expired');
        this.state.set('error');
        return;
      }
      this.expiry = setTimeout(
        () => {
          this.cleanup();
          this.providerMessage.set('Provider authentication expired');
          this.state.set('error');
        },
        Math.min(expires, 2147483647),
      );
    }
    this.loadTimeout = setTimeout(() => {
      this.onError();
    }, 25000);
    try {
      if (source.type === 'hls' && !el.canPlayType('application/vnd.apple.mpegurl')) {
        const { default: Hls } = await import('hls.js');
        if (generation !== this.generation) return;
        if (!Hls.isSupported()) throw new Error('HLS unsupported');
        this.hls = new Hls({
          manifestLoadingMaxRetry: 1,
          levelLoadingMaxRetry: 1,
          fragLoadingMaxRetry: 1,
        });
        this.hls.on(Hls.Events.ERROR, (_, data) => {
          if (data.fatal) this.onError();
        });
        this.hls.loadSource(source.url);
        this.hls.attachMedia(el);
      } else if (source.type === 'dash') {
        const dashjs = await import('dashjs');
        if (generation !== this.generation) return;
        this.dash = dashjs.MediaPlayer().create();
        this.dash.on(dashjs.MediaPlayer.events.ERROR, () => this.onError());
        this.dash.initialize(el, source.url, false);
      } else {
        el.src = source.url.startsWith('/media/')
          ? new URL(source.url.slice(1), document.baseURI).pathname
          : source.url;
        el.load();
      }
      await this.start();
    } catch {
      if (generation === this.generation) this.onError();
    }
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

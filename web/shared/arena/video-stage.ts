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
import { WhepError, WhepSession } from './whep-client';

const STREAM_STATUS: Record<StreamBrief['status'], string> = {
  live: 'Live',
  offline: 'Stream offline',
  connecting: 'Connecting',
  reconnecting: 'Reconnecting',
  error: 'Stream error',
};

/** The bundled, openly licensed clip the display falls back to. */
export const DEMO_CLIP = '/media/demo-loop.mp4';

export interface PlaybackSource {
  type: 'mp4' | 'hls' | 'dash' | 'webrtc';
  url: string;
  /** Token expiry. For live streams it only gates starting a session, never ends playback. */
  expires_at?: string | null;
  /** Live streams: Low-Latency HLS used when WebRTC cannot connect. */
  fallback_url?: string | null;
  /** Live streams: stable identity of the stream (the URL changes with every token). */
  key?: string | null;
  ice_servers?: RTCIceServer[];
}
export interface StreamBrief {
  id: string;
  name: string;
  status: 'offline' | 'connecting' | 'live' | 'reconnecting' | 'error';
  detail?: string | null;
  viewers?: number | null;
}
interface VideoResponse {
  status: string;
  message: string;
  playback?: PlaybackSource | null;
  metadata?: Record<string, unknown> | null;
  stream?: StreamBrief | null;
}

/** Reconnect delays: 1 s doubling to 30 s, with jitter so viewers do not return in waves. */
export const RECONNECT_BASE_MS = 1000;
export const RECONNECT_MAX_MS = 30000;
/** WebRTC must show a first frame within this time, or the player falls back to HLS. */
export const WEBRTC_FIRST_FRAME_MS = 8000;
/** Playback this long without an error resets the backoff. */
const STABLE_MS = 15000;
/** A playing live stream that presents no new frame for this long is treated as dead. */
export const FRAME_STALL_MS = 3000;

type Transport = 'webrtc' | 'hls' | 'file' | null;

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
              @if (state() === 'loading' && !cardIssue()) {
                <span class="spinner" aria-hidden="true"></span>
              } @else {
                <mat-icon aria-hidden="true">{{ stateIcon() }}</mat-icon>
              }
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
          @if (live()) {
            <span class="live-badge" role="status"><i aria-hidden="true"></i>LIVE</span>
          }
          <span class="source-badge" [matTooltip]="sourceTooltip()">
            <mat-icon aria-hidden="true">{{ isStream() ? 'podcasts' : 'movie' }}</mat-icon
            ><span class="source-word">{{ sourceLabel() }}</span>
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
          <input
            class="volume"
            type="range"
            min="0"
            max="1"
            step="0.05"
            [value]="muted() ? 0 : volume()"
            (input)="setVolume($event)"
            aria-label="Volume"
          />
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
          fetching() ? 'Connecting to video source' : reconnectIn() ? 'Reconnecting…' : providerStatus()
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
  private whep?: WhepSession;
  private retryTimer?: ReturnType<typeof setTimeout>;
  private frameWatch?: ReturnType<typeof setInterval>;
  private stableTimer?: ReturnType<typeof setTimeout>;
  private attempt = 0;
  /** WebRTC could not connect on this network: prefer HLS until the page reloads. */
  private webrtcBlocked = false;
  private generation = 0;
  private expiry?: ReturnType<typeof setTimeout>;
  private loadTimeout?: ReturnType<typeof setTimeout>;
  private destroyed = false;
  readonly fetching = signal(false);
  readonly providerMessage = signal('');
  readonly providerStatus = signal('');
  readonly externalMetadata = signal('');
  private readonly receivedSource = signal<PlaybackSource | null>(null);
  /** Bumped by every fresh playback answer, so a reconnect to the same stream restarts it. */
  private readonly sourceEpoch = signal(0);
  readonly forceLiveUpdates = input(false);
  readonly liveUpdatesAllowed = input(true);
  readonly event = input<EventItem | null>(null);
  readonly match = input<Match | null>(null);
  readonly announcement = input<Announcement | null>(null);
  readonly loading = input(false);
  readonly storeError = input<string | null>(null);
  /** Arena views explain a missing fight card; an administrator preview has none. */
  readonly requireMatch = input(true);
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
  readonly volume = signal(this.prefs.videoVolume());
  readonly transport = signal<Transport>(null);
  readonly streamInfo = signal<StreamBrief | null>(null);
  /** Milliseconds until the next automatic reconnect, 0 when none is scheduled. */
  readonly reconnectIn = signal(0);
  readonly state = signal<VideoState>('loading');
  readonly online = signal(typeof navigator === 'undefined' || navigator.onLine);
  readonly canFullscreen = typeof document !== 'undefined' && !!document.fullscreenEnabled;

  readonly phaseOf = matchPhase;
  readonly outcomeText = (o: keyof typeof OUTCOME_TEXT) => OUTCOME_TEXT[o];

  private readonly eventId = computed(() => this.event()?.id ?? null);
  readonly source = computed(
    () => (this.playbackSource() === undefined ? this.receivedSource() : this.playbackSource()),
    {
      // A live stream keeps its identity across token refreshes: a new token for the same
      // stream must never restart the picture.
      equal: (a, b) =>
        a?.type === 'webrtc' && b?.type === 'webrtc'
          ? a.key === b.key
          : a?.url === b?.url && a?.type === b?.type && a?.expires_at === b?.expires_at,
    },
  );
  readonly isStream = computed(() => this.source()?.type === 'webrtc');
  readonly live = computed(() => this.isStream() && this.state() === 'ready' && !!this.transport());
  readonly sourceLabel = computed(() => {
    const source = this.source();
    if (source?.type === 'webrtc') return this.transport() === 'hls' ? 'Live · HLS' : 'Live · WebRTC';
    return source?.url === '/media/demo-loop.mp4' ? 'Demo video' : 'External video';
  });
  readonly sourceTooltip = () =>
    this.isStream()
      ? `${this.streamInfo()?.name ?? 'Live stream'} · ${this.transport() === 'hls' ? 'HLS fallback' : 'low-latency WebRTC'}`
      : this.sourceLabel() === 'Demo video'
        ? 'Big Buck Bunny · Blender Foundation · CC BY 3.0'
        : 'Authorized provider source';

  /** A missing fight card is reported instead of the video's own state. */
  readonly cardIssue = computed<'loading' | 'error' | 'no-event' | 'no-fights' | null>(
    () => {
      if (this.match() || !this.requireMatch()) return null;
      // An arena stream plays (or reports its own status) before the first fight is added.
      if (this.streamInfo()) return null;
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

  /** Status of the arena's live stream while its video is not playing: what players should read. */
  readonly streamPhase = computed<'scheduled' | 'offline' | 'connecting' | 'reconnecting' | null>(() => {
    const info = this.streamInfo();
    if (!info || this.state() === 'ready' || this.state() === 'offline') return null;
    if (info.status === 'reconnecting') return 'reconnecting';
    if (info.status === 'live' || info.status === 'connecting') return 'connecting';
    const event = this.event();
    const startsAt = event?.starts_at ? Date.parse(event.starts_at) : NaN;
    if (event?.status === 'scheduled' || (event?.status !== 'live' && startsAt > Date.now())) return 'scheduled';
    return 'offline';
  });

  readonly stateIcon = computed(() => {
    switch (this.streamPhase()) {
      case 'scheduled':
        return 'event';
      case 'offline':
        return 'videocam_off';
      case 'connecting':
        return 'sensors';
      case 'reconnecting':
        return 'sync';
      default:
        break;
    }
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
    switch (this.streamPhase()) {
      case 'scheduled':
        return 'Scheduled';
      case 'offline':
        return 'Offline';
      case 'connecting':
        return 'Connecting to the live stream';
      case 'reconnecting':
        return 'Reconnecting';
      default:
        break;
    }
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
    const phase = this.streamPhase();
    if (phase === 'scheduled') {
      const at = this.event()?.starts_at;
      const when = at ? new Date(at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : null;
      return when ? `Live video starts ${when}. It appears here automatically.` : 'Live video appears here automatically when it starts.';
    }
    if (phase === 'offline') return 'The broadcast has not started yet. It appears here automatically, no refresh needed.';
    if (phase === 'connecting') return 'The broadcast is live; the video starts in a moment.';
    if (phase === 'reconnecting') return 'The broadcast was interrupted. It resumes here automatically when the camera is back.';
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
      this.sourceEpoch();
      untracked(() => {
        void this.initialize(source ?? null);
      });
    });
    // Configuration changed (enable/disable, assignment, rename): ask again, but keep the
    // current picture when the answer is the same live stream.
    const changes = this.realtime.on('video.source_changed').subscribe(() => {
      if (this.playbackSource() !== undefined) return;
      this.request?.unsubscribe();
      this.fetching.set(false);
      this.fetchSource('playback', undefined, undefined, { soft: true });
    });
    // The server's view of the source, pushed by the reconciliation task (every ~5 s):
    // "live" reconnects now instead of waiting for the backoff; "offline"/"reconnecting" means
    // the broadcaster is gone, so a session that has not noticed yet is dropped at once.
    // ("error" can mean only that the API cannot reach MediaMTX while video still flows.)
    const status = this.realtime.on('video.stream_status').subscribe((frame) => {
      const data = ((frame as { data?: unknown }).data ?? frame) as { stream_id?: string; status?: string };
      const info = this.streamInfo();
      if (!info || data?.stream_id !== info.id || this.playbackSource() !== undefined) return;
      if (data.status === 'live') {
        if (this.state() !== 'ready') {
          this.attempt = 0;
          this.retryNow();
        }
      } else if ((data.status === 'offline' || data.status === 'reconnecting') && this.isStream() && this.state() === 'ready') {
        this.cleanup();
        this.onError();
      }
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
      status.unsubscribe();
      this.clearRetry();
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
      this.isStream()
        ? `Live · ${this.transport() === 'hls' ? 'HLS' : 'WebRTC'}`
        : this.sourceLabel() === 'Demo video'
          ? 'Demo playback ready'
          : 'Playback ready',
    );
    this.state.set(this.online() ? 'ready' : 'offline');
    this.playing.set(!this.video().nativeElement.paused);
    if (this.isStream()) {
      clearTimeout(this.stableTimer);
      this.stableTimer = setTimeout(() => (this.attempt = 0), STABLE_MS);
    }
  }

  onWaiting(): void {
    clearTimeout(this.loadTimeout);
    this.loadTimeout = setTimeout(() => this.onError(), 25000);
    this.state.set(this.online() ? 'loading' : 'offline');
  }

  onError(): void {
    clearTimeout(this.loadTimeout);
    clearTimeout(this.stableTimer);
    this.providerStatus.set('Playback error');
    this.state.set(this.online() ? 'error' : 'offline');
    this.playing.set(false);
    // Live streams recover by themselves; provider files keep the explicit Try again button.
    if (this.isStream() && this.playbackSource() === undefined) this.scheduleReconnect();
  }

  /** Next attempt after 1 s, 2 s, 4 s … capped at 30 s, each ±20 %. */
  private scheduleReconnect(): void {
    if (this.destroyed || this.retryTimer || !this.online()) return;
    const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** this.attempt) * (0.8 + Math.random() * 0.4);
    this.attempt = Math.min(this.attempt + 1, 10);
    this.reconnectIn.set(Math.round(delay));
    this.retryTimer = setTimeout(() => {
      this.retryTimer = undefined;
      this.reconnectIn.set(0);
      this.fetchSource('playback');
    }, delay);
  }

  private retryNow(): void {
    this.clearRetry();
    this.fetchSource('playback');
  }

  /** Frames presented while playing; a stall means the session died without telling us. */
  private watchFrames(source: PlaybackSource, generation: number): void {
    const el = this.video().nativeElement as HTMLVideoElement & {
      requestVideoFrameCallback?: (cb: () => void) => number;
    };
    if (typeof el.requestVideoFrameCallback !== 'function') return;
    let last = performance.now();
    const tick = () => {
      last = performance.now();
      if (generation === this.generation) el.requestVideoFrameCallback?.(tick);
    };
    el.requestVideoFrameCallback(tick);
    clearInterval(this.frameWatch);
    this.frameWatch = setInterval(() => {
      if (generation !== this.generation) return clearInterval(this.frameWatch);
      if (this.state() === 'ready' && !el.paused && performance.now() - last > FRAME_STALL_MS) {
        this.streamFailed(source, generation, true);
      }
    }, 1000);
  }

  private clearRetry(): void {
    clearTimeout(this.retryTimer);
    clearTimeout(this.stableTimer);
    this.retryTimer = undefined;
    this.reconnectIn.set(0);
  }

  setVolume(event: Event): void {
    const value = Number((event.target as HTMLInputElement).value);
    const el = this.video().nativeElement;
    this.volume.set(value);
    el.volume = value;
    if (value > 0 && this.muted()) this.toggleMute();
    else if (value === 0 && !this.muted()) this.toggleMute();
    this.prefs.update({ videoVolume: value });
  }

  retry(): void {
    if (this.state() === 'blocked') void this.start();
    else this.reload();
  }

  reload(): void {
    this.clearRetry();
    this.attempt = 0;
    this.webrtcBlocked = false;
    if (this.playbackSource() !== undefined) void this.initialize(this.source() ?? null);
    else this.fetchSource('refresh');
    this.refresh.emit();
  }

  fetchSource(
    action: 'playback' | 'refresh' | 'reconnect' | 'event',
    eventId = this.event()?.id,
    audience = this.audience(),
    { soft = false }: { soft?: boolean } = {},
  ): void {
    if (!eventId || this.fetching() || this.destroyed) return;
    this.fetching.set(true);
    // A soft refetch keeps a playing live stream on screen until the answer differs.
    const keep = soft && this.isStream() && this.state() === 'ready';
    if (action !== 'event' && !keep) {
      if (action !== 'playback') this.clearRetry();
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
          this.streamInfo.set(result.stream ?? null);
          if (keep && result.playback?.type === 'webrtc' && result.playback.key === this.source()?.key) return;
          this.providerStatus.set(result.stream ? STREAM_STATUS[result.stream.status] : result.status);
          this.providerMessage.set(result.playback ? '' : result.message);
          if (keep) this.cleanup();
          this.receivedSource.set(result.playback ?? null);
          this.sourceEpoch.update((n) => n + 1);
          if (!result.playback) {
            this.state.set('error');
            // A configured stream that is not live yet (or dropped) is retried automatically.
            if (result.stream) this.scheduleReconnect();
          }
        },
        error: () => {
          this.fetching.set(false);
          if (action === 'event')
            this.externalMetadata.set('External event metadata could not be retrieved');
          else if (!keep) {
            this.providerMessage.set('Video temporarily unavailable');
            this.state.set('error');
            if (this.streamInfo()) this.scheduleReconnect();
          }
        },
      });
  }

  private cleanup(): void {
    this.generation++;
    clearTimeout(this.expiry);
    clearTimeout(this.loadTimeout);
    clearTimeout(this.stableTimer);
    clearInterval(this.frameWatch);
    this.whep?.close();
    this.whep = undefined;
    this.transport.set(null);
    this.hls?.destroy();
    this.hls = undefined;
    this.dash?.reset();
    this.dash = undefined;
    const el = this.video().nativeElement;
    el.pause();
    el.srcObject = null;
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
    if (source.type === 'webrtc') return this.startStream(source, generation);
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

  /** WebRTC first; Low-Latency HLS when WebRTC is unavailable or cannot connect here. */
  private async startStream(source: PlaybackSource, generation: number): Promise<void> {
    const el = this.video().nativeElement;
    if (this.webrtcBlocked || !WhepSession.supported() || !source.url) return this.startHls(source, generation);
    let firstFrame = false;
    const session = new WhepSession(source.url, source.ice_servers ?? [], {
      track: (stream) => {
        if (generation !== this.generation) return;
        if (el.srcObject !== stream) el.srcObject = stream;
        void this.start();
      },
      state: (state) => {
        if (generation !== this.generation) return;
        if (state === 'failed' || state === 'closed') this.streamFailed(source, generation, firstFrame);
        else if (state === 'disconnected')
          // Brief network blips recover by themselves; give ICE a moment before reconnecting.
          this.loadTimeout = setTimeout(() => {
            if (generation === this.generation && this.whep && this.state() !== 'ready') this.streamFailed(source, generation, firstFrame);
          }, 4000);
      },
    });
    this.whep = session;
    this.transport.set('webrtc');
    const onFrame = () => {
      firstFrame = true;
      clearTimeout(this.loadTimeout);
      this.watchFrames(source, generation);
    };
    el.addEventListener('loadeddata', onFrame, { once: true });
    this.loadTimeout = setTimeout(() => {
      if (generation === this.generation && !firstFrame) this.streamFailed(source, generation, false);
    }, WEBRTC_FIRST_FRAME_MS);
    try {
      await session.start();
    } catch (err) {
      if (generation !== this.generation) return;
      // 401/404: the token lapsed or the stream went offline -> ask the API again.
      if (err instanceof WhepError && err.status < 500) {
        this.cleanup();
        this.onError();
      } else this.streamFailed(source, generation, false);
    }
  }

  private streamFailed(source: PlaybackSource, generation: number, hadPicture: boolean): void {
    if (generation !== this.generation) return;
    if (!hadPicture && source.fallback_url && this.transport() === 'webrtc') {
      // WebRTC never produced a picture (blocked UDP, strict firewall): use HLS here.
      this.webrtcBlocked = true;
      this.cleanup();
      void this.startHls(source, this.generation);
      return;
    }
    this.cleanup();
    this.onError();
  }

  private async startHls(source: PlaybackSource, generation: number): Promise<void> {
    const el = this.video().nativeElement;
    const url = source.fallback_url;
    if (!url) return this.onError();
    this.transport.set('hls');
    this.loadTimeout = setTimeout(() => this.onError(), 25000);
    try {
      if (el.canPlayType('application/vnd.apple.mpegurl') && !(await this.mseAvailable())) {
        el.src = url;
        el.load();
      } else {
        const { default: Hls } = await import('hls.js');
        if (generation !== this.generation) return;
        if (!Hls.isSupported()) throw new Error('HLS unsupported');
        this.hls = new Hls({
          lowLatencyMode: true,
          backBufferLength: 0,
          liveSyncDurationCount: 1,
          maxLiveSyncPlaybackRate: 1.5,
          manifestLoadingMaxRetry: 2,
          levelLoadingMaxRetry: 2,
          fragLoadingMaxRetry: 2,
        });
        this.hls.on(Hls.Events.ERROR, (_, data) => {
          if (data.fatal && generation === this.generation) {
            this.cleanup();
            this.onError();
          }
        });
        this.hls.loadSource(url);
        this.hls.attachMedia(el);
      }
      await this.start();
    } catch {
      if (generation === this.generation) {
        this.cleanup();
        this.onError();
      }
    }
  }

  private async mseAvailable(): Promise<boolean> {
    try {
      const { default: Hls } = await import('hls.js');
      return Hls.isSupported();
    } catch {
      return false;
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
    el.volume = this.volume();
    try {
      await el.play();
      this.playing.set(true);
    } catch {
      this.playing.set(false);
      if (this.state() !== 'error' && this.state() !== 'offline') this.state.set('blocked');
    }
  }
}

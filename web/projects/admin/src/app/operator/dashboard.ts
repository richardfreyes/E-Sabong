import { DatePipe, DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatDialog } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatTooltipModule } from '@angular/material/tooltip';
import { firstValueFrom, interval } from 'rxjs';

import { type PlaybackSource, VideoStageComponent } from '@shared/arena';
import { ConfirmService, SecretRevealComponent } from '@shared/components';
import type { Broadcast, BroadcastMethod, ObsCredentials, OperatorOverview, OperatorStream, Page, Recording, Resolution } from '@shared/models';
import { OperatorApi, RealtimeService, ToastService, toApiError } from '@shared/services';

import { Broadcaster, RESOLUTIONS } from './broadcaster';
import { RecordingPlayerDialog, formatBytes, formatDuration } from './recording-player';
import { type ScreenAreaData, ScreenAreaDialog, type ScreenAreaResult } from './screen-area-dialog';

/** Camera and Screen broadcast from this browser (WHIP); OBS publishes from an external encoder. */
type Mode = 'camera' | 'screen' | 'obs';
type Health = 'good' | 'fair' | 'poor' | 'idle';
const STREAM_KEY = 'esabong.operator.stream';
const METHOD_LABEL: Record<BroadcastMethod, string> = { browser: 'Browser camera', obs: 'OBS / encoder', external: 'External source', test: 'Test pattern' };

/**
 * Stream Operator dashboard: broadcast from this browser (camera + microphone over WHIP) or
 * from OBS (RTMP/RTMPS/WHIP), record, and review recordings. Only assigned streams appear.
 */
@Component({
  selector: 'app-operator-dashboard',
  imports: [
    DatePipe, DecimalPipe, FormsModule, MatButtonModule, MatButtonToggleModule, MatFormFieldModule, MatIconModule, MatSelectModule,
    MatSlideToggleModule, MatTooltipModule, SecretRevealComponent, VideoStageComponent,
  ],
  providers: [Broadcaster],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
  host: { '(window:beforeunload)': 'beforeUnload($event)' },
})
export class OperatorDashboard {
  private readonly api = inject(OperatorApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly dialog = inject(MatDialog);
  readonly broadcaster = inject(Broadcaster);
  readonly resolutions = RESOLUTIONS;
  readonly resolutionKeys = Object.keys(RESOLUTIONS) as Resolution[];
  readonly origin = typeof location === 'undefined' ? '' : location.origin;
  readonly formatBytes = formatBytes;
  readonly formatDuration = formatDuration;
  readonly methodLabel = METHOD_LABEL;

  readonly overview = signal<OperatorOverview | null>(null);
  readonly loadError = signal<string | null>(null);
  readonly selectedId = signal<string | null>(readStored());
  readonly mode = signal<Mode>('camera');
  /** Broadcasting from this browser (camera or screen). */
  readonly local = computed(() => this.mode() !== 'obs');
  readonly now = signal(Date.now());
  readonly actionBusy = signal(false);
  readonly obsCredentials = signal<ObsCredentials | null>(null);
  readonly monitorSource = signal<PlaybackSource | null>(null);
  readonly monitorMessage = signal<string>('');
  readonly showMonitor = signal(false);
  readonly recordings = signal<Page<Recording> | null>(null);
  readonly recordingsPage = signal(1);
  readonly broadcasts = signal<Page<Broadcast> | null>(null);
  readonly broadcastsPage = signal(1);

  readonly preview = viewChild<ElementRef<HTMLVideoElement>>('preview');

  readonly streams = computed(() => this.overview()?.streams ?? []);
  readonly server = computed(() => this.overview()?.server ?? null);
  readonly stream = computed<OperatorStream | null>(() => {
    const list = this.streams();
    return list.find((s) => s.id === this.selectedId()) ?? list[0] ?? null;
  });
  readonly broadcast = computed(() => this.stream()?.broadcast ?? null);
  readonly recording = computed(() => this.stream()?.recording ?? null);
  /** This browser is (or should be) the publisher of the selected stream. */
  readonly mineLive = computed(() => this.broadcaster.publishing());
  /** My browser session is open on the server but this tab is not publishing (page reloaded). */
  readonly resumable = computed(() => {
    const b = this.broadcast();
    return !!b && b.mine && b.method === 'browser' && !this.broadcaster.publishing();
  });
  readonly otherPublisher = computed(() => {
    const b = this.broadcast();
    return b && !b.mine && !this.broadcaster.publishing() ? b : null;
  });

  /** Offline / Connecting / Live / Error, as the operator should read it. */
  readonly displayState = computed<'offline' | 'connecting' | 'live' | 'reconnecting' | 'error'>(() => {
    const local = this.broadcaster.state();
    if (this.local() && local !== 'offline') {
      if (local === 'live' && this.stream()?.status !== 'live') return 'connecting';
      return local;
    }
    return this.stream()?.status ?? 'offline';
  });
  readonly stateLabel = computed(() => ({
    offline: 'Offline', connecting: 'Connecting', live: 'Live', reconnecting: 'Reconnecting', error: 'Error',
  })[this.displayState()]);

  readonly liveSeconds = computed(() => {
    const b = this.broadcast();
    const since = b?.live_at ? Date.parse(b.live_at) : this.broadcaster.liveSince();
    return since && (this.displayState() === 'live' || this.displayState() === 'reconnecting') ? Math.max(0, (this.now() - since) / 1000) : null;
  });
  readonly recordingSeconds = computed(() => {
    const r = this.recording();
    return r && r.status === 'recording' ? Math.max(0, (this.now() - Date.parse(r.started_at)) / 1000) : null;
  });

  readonly bitrate = computed(() => {
    if (this.local() && this.broadcaster.publishing()) return this.broadcaster.stats().bitrateKbps;
    return this.stream()?.inbound_kbps ?? null;
  });

  readonly health = computed<{ label: string; level: Health; detail: string }[]>(() => {
    const server = this.server();
    const stream = this.stream();
    const stats = this.broadcaster.stats();
    const publishing = this.local() && this.broadcaster.publishing();
    const out: { label: string; level: Health; detail: string }[] = [];
    out.push(
      !server?.configured
        ? { label: 'Streaming server', level: 'poor', detail: 'Not configured on this server' }
        : server.reachable
          ? { label: 'Streaming server', level: 'good', detail: 'Online' }
          : { label: 'Streaming server', level: 'poor', detail: 'Unreachable' },
    );
    if (publishing) {
      const loss = stats.lossPercent ?? 0;
      const rtt = stats.rttMs ?? 0;
      out.push({
        label: 'Network',
        level: loss > 5 || rtt > 400 ? 'poor' : loss > 1 || rtt > 200 ? 'fair' : 'good',
        detail: `${stats.rttMs ?? '—'} ms RTT · ${stats.lossPercent ?? 0}% loss`,
      });
      const target = RESOLUTIONS[this.broadcaster.resolution()].height;
      out.push({
        label: 'Encoder',
        level: stats.limitation ? 'fair' : stats.height !== null && stats.height < target * 0.66 ? 'fair' : 'good',
        detail: stats.limitation ? `Lowering quality (${stats.limitation})` : `${stats.width ?? '—'}×${stats.height ?? '—'} @ ${stats.fps ?? '—'} fps`,
      });
    } else {
      out.push({ label: 'Ingest', level: stream?.status === 'live' ? 'good' : stream?.status === 'error' ? 'poor' : 'idle',
        detail: stream?.status === 'live' ? `${stream.tracks?.join(' + ') ?? 'Receiving'}` : stream?.status_detail ?? 'No source connected' });
    }
    out.push({
      label: 'Player delivery',
      level: stream?.status !== 'live' ? 'idle' : stream.webrtc_ready ? 'good' : 'fair',
      detail: stream?.status !== 'live' ? 'Waiting for the stream' : stream.webrtc_ready ? 'WebRTC + HLS ready' : 'HLS only for now',
    });
    out.push(
      server?.recording_available
        ? { label: 'Recording storage', level: 'good', detail: `${formatBytes(server.used_bytes)} of ${formatBytes(server.quota_bytes)} used` }
        : { label: 'Recording storage', level: 'poor', detail: server?.recording_problem ?? 'Unavailable' },
    );
    return out;
  });

  constructor() {
    void this.load();
    void this.loadRecordings();
    void this.loadBroadcasts();
    interval(1000).pipe(takeUntilDestroyed()).subscribe(() => this.now.set(Date.now()));
    interval(5000).pipe(takeUntilDestroyed()).subscribe(() => void this.load());
    const realtime = inject(RealtimeService);
    realtime.on('video.stream_status', 'video.assignments').pipe(takeUntilDestroyed()).subscribe(() => void this.load());
    realtime.on('video.broadcast').pipe(takeUntilDestroyed()).subscribe(() => {
      void this.load();
      void this.loadBroadcasts();
    });
    realtime.on('video.recording').pipe(takeUntilDestroyed()).subscribe(() => {
      void this.load();
      void this.loadRecordings();
    });
    // The local preview element follows the camera stream.
    effect(() => {
      const el = this.preview()?.nativeElement;
      const media = this.broadcaster.preview();
      if (el && el.srcObject !== media) el.srcObject = media;
    });
    // Default the mode to what the stream supports.
    effect(() => {
      const s = this.stream();
      if (s && !s.browser_publishing && this.local()) this.mode.set('obs');
    });
    inject(DestroyRef).onDestroy(() => this.broadcaster.closePreview());
  }

  async load(): Promise<void> {
    try {
      this.overview.set(await firstValueFrom(this.api.overview()));
      this.loadError.set(null);
    } catch (err) {
      this.loadError.set(toApiError(err).message);
    }
  }

  async loadRecordings(page = this.recordingsPage()): Promise<void> {
    try {
      this.recordings.set(await firstValueFrom(this.api.recordings({ page, page_size: 10 })));
      this.recordingsPage.set(page);
    } catch (err) {
      this.toast.error(err);
    }
  }

  async loadBroadcasts(page = this.broadcastsPage()): Promise<void> {
    try {
      this.broadcasts.set(await firstValueFrom(this.api.broadcasts({ page, page_size: 10 })));
      this.broadcastsPage.set(page);
    } catch {
      /* history is secondary; the next realtime event retries */
    }
  }

  /** On-air time of a session (from going live; still running sessions count up). */
  sessionSeconds(b: Broadcast): number | null {
    const start = Date.parse(b.live_at ?? b.started_at);
    const end = b.ended_at ? Date.parse(b.ended_at) : this.now();
    return b.live_at || b.ended_at ? Math.max(0, (end - start) / 1000) : null;
  }

  select(id: string): void {
    if (this.broadcaster.publishing()) {
      this.toast.info('Stop the current broadcast before switching streams');
      return;
    }
    this.selectedId.set(id);
    this.obsCredentials.set(null);
    this.showMonitor.set(false);
    try { localStorage.setItem(STREAM_KEY, id); } catch { /* private mode */ }
  }

  // ---- devices

  async enableCamera(): Promise<void> {
    await this.broadcaster.openPreview();
  }

  async changeDevice(kind: 'camera' | 'microphone', id: string): Promise<void> {
    (kind === 'camera' ? this.broadcaster.cameraId : this.broadcaster.microphoneId).set(id);
    await this.broadcaster.applyDevices();
  }

  /** Camera ↔ screen switch the browser capture (live broadcasts keep running); OBS hands over to an encoder. */
  async changeMode(value: Mode): Promise<void> {
    if (value === 'obs') {
      if (this.broadcaster.publishing()) return;
      this.mode.set('obs');
      return;
    }
    this.mode.set(value);
    await this.broadcaster.setSource(value);
    // A failed or cancelled switch leaves the capture as it was; keep the toggle honest.
    if (this.broadcaster.source() !== value) this.mode.set(this.broadcaster.source());
  }

  async chooseScreen(): Promise<void> {
    await this.broadcaster.chooseScreen();
  }

  /** Pick the part of the shared screen players see; applies at once, also while live. */
  async selectArea(): Promise<void> {
    const stream = this.broadcaster.rawScreen();
    if (!stream) return;
    const ref = this.dialog.open<ScreenAreaDialog, ScreenAreaData, ScreenAreaResult>(ScreenAreaDialog, {
      data: { stream, area: this.broadcaster.area() },
      width: '1100px',
      maxWidth: '96vw',
      autoFocus: 'dialog',
    });
    const result = await firstValueFrom(ref.afterClosed());
    if (result === undefined) return;
    await this.broadcaster.setArea(result);
    this.toast.info(result ? 'Broadcasting the selected area' : 'Broadcasting the whole screen');
  }

  async changeResolution(value: Resolution): Promise<void> {
    if (this.broadcaster.publishing()) {
      this.toast.info('Stop the broadcast to change the resolution');
      return;
    }
    this.broadcaster.resolution.set(value);
    await this.broadcaster.applyDevices();
  }

  // ---- streaming

  async startStreaming(): Promise<void> {
    const s = this.stream();
    if (!s) return;
    try {
      await this.broadcaster.start(s.id);
      this.toast.success('Broadcast started');
    } catch (err) {
      const e = toApiError(err);
      if (e.code !== 'stream_busy') return; // the broadcaster shows the error
      const ok = await this.confirm.ask({
        title: 'Take over this stream?',
        message: 'Someone is already broadcasting here. Taking over replaces their video for every viewer.',
        confirmText: 'Take over',
        destructive: true,
      });
      if (ok === null) {
        this.broadcaster.error.set(null);
        this.broadcaster.state.set('offline');
        return;
      }
      await this.broadcaster.start(s.id, true).catch(() => undefined);
    }
    await this.load();
  }

  async stopStreaming(): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Stop the broadcast?',
      message: this.recording()?.status === 'recording'
        ? 'Players lose the video immediately. The recording keeps running and resumes when you start again; stop it separately.'
        : 'Players lose the video immediately.',
      confirmText: 'Stop broadcast',
      destructive: true,
    });
    if (ok === null) return;
    await this.broadcaster.stop();
    this.toast.info('Broadcast stopped');
    await this.load();
  }

  async resume(): Promise<void> {
    const s = this.stream();
    if (!s) return;
    if (!this.broadcaster.preview()) await this.broadcaster.openPreview();
    if (this.broadcaster.preview()) this.broadcaster.resume(s.id);
  }

  async serverReconnect(): Promise<void> {
    const s = this.stream();
    if (!s) return;
    await this.run(async () => {
      await firstValueFrom(this.api.reconnect(s.id));
      this.toast.info(s.source_kind === 'obs' ? 'Encoder disconnected; OBS reconnects automatically' : 'Reconnecting to the source');
    });
  }

  async issueObsKey(): Promise<void> {
    const s = this.stream();
    if (!s) return;
    const ok = await this.confirm.ask({
      title: 'Generate a new OBS stream key?',
      message: 'The previous key stops working at once and an encoder using it is disconnected. The new key is shown only once.',
      confirmText: 'Generate key',
    });
    if (ok === null) return;
    await this.run(async () => this.obsCredentials.set(await firstValueFrom(this.api.obsKey(s.id))));
  }

  async toggleMonitor(): Promise<void> {
    const s = this.stream();
    if (!s) return;
    if (this.showMonitor()) {
      this.showMonitor.set(false);
      this.monitorSource.set(null);
      return;
    }
    this.showMonitor.set(true);
    this.monitorMessage.set('Requesting playback…');
    try {
      const result = await firstValueFrom(this.api.preview(s.id));
      this.monitorSource.set(result.playback);
      if (!result.playback) this.monitorMessage.set(result.stream?.detail ?? result.message);
    } catch (err) {
      this.monitorMessage.set(toApiError(err).message);
    }
  }

  // ---- recording

  async startRecording(): Promise<void> {
    const s = this.stream();
    if (!s) return;
    await this.run(async () => {
      await firstValueFrom(this.api.startRecording(s.id));
      this.toast.success('Recording started');
      await Promise.all([this.load(), this.loadRecordings(1)]);
    });
  }

  async stopRecording(): Promise<void> {
    const r = this.recording();
    if (!r) return;
    await this.run(async () => {
      await firstValueFrom(this.api.stopRecording(r.id));
      this.toast.info('Recording stopped; it appears below once saved');
      await Promise.all([this.load(), this.loadRecordings(1)]);
    });
  }

  pageCount(page: Page<unknown>): number {
    return Math.max(1, Math.ceil(page.total / page.page_size));
  }

  open(r: Recording): void {
    this.dialog.open(RecordingPlayerDialog, { data: r, width: '960px', maxWidth: '96vw', autoFocus: 'dialog' });
  }

  beforeUnload(event: BeforeUnloadEvent): void {
    if (this.broadcaster.publishing()) event.preventDefault();
  }

  private async run(action: () => Promise<void>): Promise<void> {
    if (this.actionBusy()) return;
    this.actionBusy.set(true);
    try {
      await action();
    } catch (err) {
      this.toast.error(toApiError(err));
    } finally {
      this.actionBusy.set(false);
    }
  }
}

function readStored(): string | null {
  try {
    return localStorage.getItem(STREAM_KEY);
  } catch {
    return null;
  }
}

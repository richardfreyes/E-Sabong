import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import type { PublishGrant, Resolution, Uuid } from '@shared/models';
import { OperatorApi, toApiError } from '@shared/services';

import { type CropRect, type Cropper, clampCrop, createCropper, isFullFrame, pixelRect } from './screen-crop';
import { type PublishStats, WhipError, WhipPublisher } from './whip-publisher';

/** What the operator sees for the browser broadcast (the server's own view is shown beside it). */
export type LocalState = 'offline' | 'connecting' | 'live' | 'reconnecting' | 'error';
export type DeviceAccess = 'unknown' | 'prompt' | 'granted' | 'denied' | 'unavailable';
/** What the browser broadcast captures: a camera, or a screen / window / tab picked by the operator. */
export type CaptureSource = 'camera' | 'screen';

export const RESOLUTIONS: Record<Resolution, { width: number; height: number; label: string }> = {
  '720p': { width: 1280, height: 720, label: '720p HD (2.5 Mbps)' },
  '1080p': { width: 1920, height: 1080, label: '1080p Full HD (4.5 Mbps)' },
};
const MAX_RECONNECTS = 6;
const AREA_KEY = 'esabong.operator.screenArea';
/** Screens are captured up to 1080p so a selected area keeps detail; the encoder scales down. */
const SCREEN_CAPTURE = { width: 1920, height: 1080 };

function readArea(): CropRect | null {
  try {
    const raw = JSON.parse(localStorage.getItem(AREA_KEY) ?? 'null');
    return raw && typeof raw.w === 'number' ? clampCrop(raw) : null;
  } catch {
    return null;
  }
}

function saveArea(crop: CropRect | null): void {
  try {
    if (crop) localStorage.setItem(AREA_KEY, JSON.stringify(crop));
    else localStorage.removeItem(AREA_KEY);
  } catch {
    /* private mode */
  }
}

interface SharedScreen {
  /** The screen/window/tab exactly as captured. */
  raw: MediaStreamTrack;
  /** Present while an area is selected; its track is what is previewed and broadcast. */
  cropper: Cropper | null;
}
const EMPTY_STATS: PublishStats = { bitrateKbps: null, fps: null, width: null, height: null, rttMs: null, lossPercent: null, limitation: null };

/** Human message for getUserMedia / WHIP failures. */
export function mediaErrorMessage(err: unknown): string {
  const name = (err as { name?: string })?.name;
  switch (name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'Camera or microphone access was blocked. Allow it in the browser’s site settings, then try again.';
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'The selected camera or microphone was not found. Pick another device.';
    case 'NotReadableError':
    case 'AbortError':
      return 'The camera or microphone is in use by another application. Close it and try again.';
  }
  if (err instanceof WhipError) return err.message;
  return toApiError(err).message || 'Something went wrong while starting the camera.';
}

/**
 * Browser broadcast for one stream: devices and local preview, WHIP publishing with automatic
 * reconnection, and encoder statistics. One instance per Operator dashboard.
 */
@Injectable()
export class Broadcaster {
  private readonly api = inject(OperatorApi);
  private publisher: WhipPublisher | null = null;
  private streamId: Uuid | null = null;
  private maxBitrate = 2500;
  private reconnects = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private statsTimer: ReturnType<typeof setInterval> | null = null;
  private audioContext: AudioContext | null = null;
  private meterFrame = 0;
  private disconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private screen: SharedScreen | null = null;
  private pendingScreen: SharedScreen | null = null;
  /** Replaceable in tests (no media pipeline there). */
  cropperFactory: (source: MediaStreamTrack, crop: CropRect) => Cropper = createCropper;

  readonly cameras = signal<MediaDeviceInfo[]>([]);
  readonly microphones = signal<MediaDeviceInfo[]>([]);
  readonly cameraId = signal<string>('');
  readonly microphoneId = signal<string>('');
  readonly resolution = signal<Resolution>('720p');
  readonly access = signal<DeviceAccess>('unknown');
  readonly preview = signal<MediaStream | null>(null);
  readonly state = signal<LocalState>('offline');
  readonly error = signal<string | null>(null);
  readonly stats = signal<PublishStats>(EMPTY_STATS);
  readonly audioLevel = signal(0);
  readonly liveSince = signal<number | null>(null);
  readonly busy = signal(false);
  readonly reconnectAttempt = signal(0);
  readonly supported = WhipPublisher.supported();
  readonly source = signal<CaptureSource>('camera');
  /** Mix the selected microphone into a screen broadcast (else the shared tab/system audio, if any). */
  readonly includeMicrophone = signal(true);
  readonly screenSupported = typeof navigator !== 'undefined' && typeof navigator.mediaDevices?.getDisplayMedia === 'function';
  /** The selected area of the shared screen (fractions of it), or null for the whole picture. */
  readonly area = signal<CropRect | null>(readArea());
  /** The shared screen as captured (uncropped), for the area editor. */
  readonly rawScreen = signal<MediaStream | null>(null);
  /** How the area is cut out: 'canvas' needs this tab to stay visible while broadcasting. */
  readonly cropMethod = signal<'frames' | 'canvas' | null>(null);
  /** Label of the shared screen/window/tab, for the operator. */
  readonly sharing = computed(() => (this.source() === 'screen' ? (this.rawScreen()?.getVideoTracks()[0]?.label ?? null) : null));
  readonly publishing = computed(() => this.state() !== 'offline' && this.state() !== 'error');
  readonly captured = computed(() => {
    const raw = this.source() === 'screen' ? this.rawScreen()?.getVideoTracks()[0]?.getSettings() : null;
    const area = this.area();
    if (raw?.width && raw?.height) {
      if (isFullFrame(area)) return `${raw.width}×${raw.height}`;
      const r = pixelRect(area!, raw.width, raw.height);
      return `${r.width}×${r.height} area`;
    }
    const video = this.preview()?.getVideoTracks()[0]?.getSettings();
    return video?.width && video?.height ? `${video.width}×${video.height}` : null;
  });

  constructor() {
    inject(DestroyRef).onDestroy(() => this.dispose());
    if (typeof navigator !== 'undefined' && navigator.mediaDevices) {
      navigator.mediaDevices.addEventListener?.('devicechange', this.onDeviceChange);
    }
  }

  private readonly onDeviceChange = (): void => void this.listDevices();

  /** Ask for camera + microphone (shows the browser prompt the first time) and start the preview. */
  async openPreview(): Promise<void> {
    if (!this.supported) {
      this.access.set('unavailable');
      this.error.set('This browser cannot broadcast. Use a current Chrome, Edge, Firefox or Safari over HTTPS.');
      return;
    }
    this.error.set(null);
    try {
      const media = await this.capture();
      await this.swap(media);
      this.access.set('granted');
      await this.listDevices();
    } catch (err) {
      if (this.source() === 'screen' && (err as { name?: string })?.name === 'NotAllowedError') {
        this.error.set('Screen sharing was cancelled. Click “Choose screen to share” to pick a screen, window or tab.');
        return;
      }
      this.access.set((err as { name?: string })?.name === 'NotAllowedError' ? 'denied' : 'unknown');
      this.error.set(mediaErrorMessage(err));
    }
  }

  /**
   * Switch between camera and screen. While live the new picture replaces the old one on the same
   * connection (viewers are not disconnected); otherwise only the preview changes.
   */
  async setSource(source: CaptureSource): Promise<void> {
    if (source === this.source()) return;
    const previous = this.source();
    this.source.set(source);
    if (!this.preview()) return;
    try {
      await this.swap(await this.capture());
    } catch (err) {
      this.source.set(previous);
      if ((err as { name?: string })?.name !== 'NotAllowedError') this.error.set(mediaErrorMessage(err));
    }
  }

  /** Pick a different screen, window or tab (screen source only). */
  async chooseScreen(): Promise<void> {
    if (this.source() !== 'screen') return;
    if (!this.preview()) return this.openPreview();
    try {
      await this.swap(await this.capture());
    } catch (err) {
      if ((err as { name?: string })?.name !== 'NotAllowedError') this.error.set(mediaErrorMessage(err));
    }
  }

  /** Re-open the selected devices; a live broadcast switches tracks without reconnecting. */
  async applyDevices(): Promise<void> {
    if (this.access() !== 'granted') return;
    try {
      if (this.source() === 'screen') {
        // Keep the shared screen: only the microphone (and the screen's size cap) change.
        const video = this.preview()?.getVideoTracks()[0];
        if (!video) return;
        const audio = await this.microphoneTrack();
        await this.swap(new MediaStream(audio ? [video, audio] : [video]), { keepVideo: true });
        await this.updateScale();
      } else {
        await this.swap(await navigator.mediaDevices.getUserMedia(this.constraints()));
      }
      this.error.set(null);
    } catch (err) {
      this.error.set(mediaErrorMessage(err));
    }
  }

  async start(streamId: Uuid, takeover = false): Promise<void> {
    if (this.busy()) return;
    if (!this.preview()) await this.openPreview();
    if (!this.preview()) return;
    this.busy.set(true);
    this.error.set(null);
    this.streamId = streamId;
    this.reconnects = 0;
    this.reconnectAttempt.set(0);
    this.state.set('connecting');
    try {
      const grant = await firstValueFrom(this.api.startBroadcast(streamId, this.resolution(), takeover));
      await this.connect(grant);
    } catch (err) {
      this.fail(err);
      throw err;
    } finally {
      this.busy.set(false);
    }
  }

  async stop(): Promise<void> {
    const streamId = this.streamId;
    this.teardown();
    this.state.set('offline');
    this.liveSince.set(null);
    this.streamId = null;
    if (streamId) await firstValueFrom(this.api.stopBroadcast(streamId)).catch(() => undefined);
  }

  /** Manual reconnect after the automatic attempts gave up. */
  async reconnectNow(): Promise<void> {
    if (!this.streamId) return;
    this.reconnects = 0;
    await this.reconnect();
  }

  /** Releases the camera (preview off) when not broadcasting. */
  closePreview(): void {
    if (this.publishing()) return;
    this.stopMeter();
    this.preview()?.getTracks().forEach((t) => t.stop());
    this.preview.set(null);
    this.releaseScreen();
  }

  /**
   * Broadcast only ``area`` of the shared screen (null = all of it). Applies at once, also while
   * live, without reconnecting viewers; remembered for the next session.
   */
  async setArea(area: CropRect | null): Promise<void> {
    const crop = area && !isFullFrame(area) ? clampCrop(area) : null;
    this.area.set(crop);
    saveArea(crop);
    const screen = this.screen;
    if (!screen) return;
    if (crop && screen.cropper) {
      screen.cropper.setCrop(crop);
    } else if (crop) {
      screen.cropper = this.cropperFactory(screen.raw, crop);
      await this.replaceVideo(screen.cropper.track);
    } else if (screen.cropper) {
      const old = screen.cropper;
      screen.cropper = null;
      await this.replaceVideo(screen.raw);
      old.stop();
    }
    this.cropMethod.set(screen.cropper?.method ?? null);
    await this.updateScale();
  }

  private async replaceVideo(track: MediaStreamTrack): Promise<void> {
    this.preview.set(new MediaStream([track, ...(this.preview()?.getAudioTracks() ?? [])]));
    if (this.publisher) await this.publisher.replaceTrack(track);
  }

  /** Keep the encoded picture at most the chosen resolution (a large area or screen is scaled down). */
  private async updateScale(): Promise<void> {
    if (!this.publisher) return;
    let height: number | undefined;
    const raw = this.screen?.raw.getSettings();
    if (this.source() === 'screen' && raw?.width && raw?.height) {
      const area = this.area();
      height = isFullFrame(area) ? raw.height : pixelRect(area!, raw.width, raw.height).height;
    } else {
      height = this.preview()?.getVideoTracks()[0]?.getSettings().height;
    }
    const target = RESOLUTIONS[this.resolution()].height;
    // Quality tuning only: never let it interrupt a source switch or a reconnect.
    await this.publisher.setScaleDown(height ? Math.max(1, height / target) : 1).catch(() => undefined);
  }

  private releaseScreen(): void {
    this.screen?.cropper?.stop();
    this.screen?.raw.stop();
    this.screen = null;
    this.rawScreen.set(null);
    this.cropMethod.set(null);
  }

  /** Adopt a broadcast this operator already has open (page reload while live). */
  resume(streamId: Uuid): void {
    this.streamId = streamId;
    this.state.set('reconnecting');
    void this.reconnect();
  }

  private async capture(): Promise<MediaStream> {
    if (this.source() === 'camera') return navigator.mediaDevices.getUserMedia(this.constraints());
    if (!this.screenSupported) throw new Error('This browser cannot share the screen. Use a current desktop Chrome, Edge, Firefox or Safari.');
    const display = await navigator.mediaDevices.getDisplayMedia({
      video: { width: { max: SCREEN_CAPTURE.width }, height: { max: SCREEN_CAPTURE.height }, frameRate: { ideal: 30, max: 30 } },
      audio: true,
    });
    const video = display.getVideoTracks()[0];
    // Shared screens usually show moving video (the arena feed): favour smooth motion over sharp text.
    video.contentHint = 'motion';
    video.addEventListener('ended', this.onScreenEnded, { once: true });
    const audio = (this.includeMicrophone() ? await this.microphoneTrack() : null) ?? display.getAudioTracks()[0] ?? null;
    if (audio && audio !== display.getAudioTracks()[0]) display.getAudioTracks().forEach((t) => t.stop());
    const area = this.area();
    const cropper = isFullFrame(area) ? null : this.cropperFactory(video, area!);
    this.pendingScreen = { raw: video, cropper };
    const out = cropper?.track ?? video;
    return new MediaStream(audio ? [out, audio] : [out]);
  }

  private async microphoneTrack(): Promise<MediaStreamTrack | null> {
    try {
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { ...(this.microphoneId() ? { deviceId: { exact: this.microphoneId() } } : {}), echoCancellation: true, noiseSuppression: true },
      });
      return mic.getAudioTracks()[0] ?? null;
    } catch {
      return null; // broadcast the screen without a microphone rather than failing
    }
  }

  /** Make ``media`` the preview and (when live) the broadcast, then release the old tracks. */
  private async swap(media: MediaStream, { keepVideo = false }: { keepVideo?: boolean } = {}): Promise<void> {
    const previous = this.preview();
    // A screen without audio keeps the previous microphone, so viewers never lose sound mid-broadcast.
    const oldAudio = previous?.getAudioTracks()[0];
    if (!media.getAudioTracks().length && oldAudio && oldAudio.readyState === 'live') media.addTrack(oldAudio);
    this.setPreview(media);
    if (!keepVideo) {
      const old = this.screen;
      this.screen = this.pendingScreen;
      this.pendingScreen = null;
      this.rawScreen.set(this.screen ? new MediaStream([this.screen.raw]) : null);
      this.cropMethod.set(this.screen?.cropper?.method ?? null);
      if (old && old !== this.screen) {
        old.cropper?.stop();
        old.raw.stop();
      }
    }
    if (this.publisher) {
      for (const track of media.getTracks()) await this.publisher.replaceTrack(track);
      await this.updateScale();
    }
    const keep = new Set(media.getTracks());
    previous?.getTracks().forEach((t) => {
      if (!keep.has(t) && !(keepVideo && t.kind === 'video')) t.stop();
    });
  }

  /** The operator pressed the browser's own "Stop sharing". */
  private readonly onScreenEnded = (): void => {
    if (this.source() !== 'screen' || this.screen?.raw.readyState === 'live') return;
    if (this.publishing()) {
      void this.stop();
      this.error.set('Screen sharing ended, so the broadcast was stopped. Choose a screen and start streaming again.');
    } else {
      this.closePreview();
    }
  };

  private async connect(grant: PublishGrant): Promise<void> {
    const media = this.preview();
    if (!media) throw new Error('Camera is not available');
    this.teardownPublisher();
    this.maxBitrate = grant.max_bitrate_kbps;
    const publisher = new WhipPublisher(grant.whip_url, grant.ice_servers, { state: (s) => this.onPeerState(publisher, s) });
    this.publisher = publisher;
    await publisher.start(media, this.maxBitrate);
    await this.updateScale();
    this.startStats();
  }

  private onPeerState(publisher: WhipPublisher, s: RTCPeerConnectionState): void {
    if (publisher !== this.publisher) return;
    if (s === 'connected') {
      if (this.disconnectTimer) clearTimeout(this.disconnectTimer);
      this.disconnectTimer = null;
      this.state.set('live');
      this.reconnects = 0;
      this.reconnectAttempt.set(0);
      if (this.liveSince() === null) this.liveSince.set(Date.now());
    } else if (s === 'failed') {
      void this.reconnect();
    } else if (s === 'disconnected') {
      // ICE often recovers by itself within a few seconds; reconnect only if it does not.
      this.state.set('reconnecting');
      this.disconnectTimer ??= setTimeout(() => {
        this.disconnectTimer = null;
        if (this.publisher === publisher && publisher && this.state() === 'reconnecting') void this.reconnect();
      }, 4000);
    }
  }

  private async reconnect(): Promise<void> {
    if (!this.streamId) return;
    this.teardownPublisher();
    if (this.reconnects >= MAX_RECONNECTS) {
      this.state.set('error');
      this.error.set('The connection to the streaming server was lost. Check the network, then press Reconnect.');
      return;
    }
    this.reconnects += 1;
    this.reconnectAttempt.set(this.reconnects);
    this.state.set('reconnecting');
    try {
      if (!this.preview()) await this.openPreview();
      const grant = await firstValueFrom(this.api.broadcastToken(this.streamId));
      await this.connect(grant);
    } catch (err) {
      const code = toApiError(err).code;
      if (code === 'broadcast_ended') {
        // Stopped elsewhere (another operator took over, or an administrator unassigned it).
        this.teardown();
        this.streamId = null;
        this.liveSince.set(null);
        this.state.set('offline');
        this.error.set('This broadcast was ended from another session.');
        return;
      }
      const delay = Math.min(15000, 1000 * 2 ** (this.reconnects - 1));
      this.retryTimer = setTimeout(() => void this.reconnect(), delay);
    }
  }

  private fail(err: unknown): void {
    this.teardownPublisher();
    this.state.set('error');
    this.error.set(mediaErrorMessage(err));
  }

  private startStats(): void {
    if (this.statsTimer) return;
    this.statsTimer = setInterval(async () => {
      if (this.publisher) this.stats.set(await this.publisher.stats().catch(() => EMPTY_STATS));
    }, 2000);
  }

  private constraints(): MediaStreamConstraints {
    const r = RESOLUTIONS[this.resolution()];
    return {
      video: {
        ...(this.cameraId() ? { deviceId: { exact: this.cameraId() } } : {}),
        width: { ideal: r.width },
        height: { ideal: r.height },
        frameRate: { ideal: 30, max: 30 },
      },
      audio: {
        ...(this.microphoneId() ? { deviceId: { exact: this.microphoneId() } } : {}),
        echoCancellation: true,
        noiseSuppression: true,
      },
    };
  }

  private setPreview(media: MediaStream): void {
    this.preview.set(media);
    const video = media.getVideoTracks()[0]?.getSettings().deviceId;
    const audio = media.getAudioTracks()[0]?.getSettings().deviceId;
    if (video && this.source() === 'camera') this.cameraId.set(video);
    if (audio) this.microphoneId.set(audio);
    this.startMeter(media);
  }

  private async listDevices(): Promise<void> {
    const devices = await navigator.mediaDevices.enumerateDevices();
    this.cameras.set(devices.filter((d) => d.kind === 'videoinput' && d.deviceId));
    this.microphones.set(devices.filter((d) => d.kind === 'audioinput' && d.deviceId));
  }

  private startMeter(media: MediaStream): void {
    this.stopMeter();
    const track = media.getAudioTracks()[0];
    if (!track || typeof AudioContext === 'undefined') return;
    this.audioContext = new AudioContext();
    const analyser = this.audioContext.createAnalyser();
    analyser.fftSize = 512;
    this.audioContext.createMediaStreamSource(new MediaStream([track])).connect(analyser);
    const data = new Uint8Array(analyser.fftSize);
    const tick = () => {
      analyser.getByteTimeDomainData(data);
      let peak = 0;
      for (const v of data) peak = Math.max(peak, Math.abs(v - 128));
      this.audioLevel.set(Math.min(1, peak / 100));
      this.meterFrame = requestAnimationFrame(tick);
    };
    tick();
  }

  private stopMeter(): void {
    cancelAnimationFrame(this.meterFrame);
    void this.audioContext?.close().catch(() => undefined);
    this.audioContext = null;
    this.audioLevel.set(0);
  }

  private teardownPublisher(): void {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.disconnectTimer) clearTimeout(this.disconnectTimer);
    this.retryTimer = this.disconnectTimer = null;
    this.publisher?.close();
    this.publisher = null;
  }

  private teardown(): void {
    this.teardownPublisher();
    if (this.statsTimer) clearInterval(this.statsTimer);
    this.statsTimer = null;
    this.stats.set(EMPTY_STATS);
  }

  private dispose(): void {
    navigator.mediaDevices?.removeEventListener?.('devicechange', this.onDeviceChange);
    this.teardown();
    this.stopMeter();
    this.preview()?.getTracks().forEach((t) => t.stop());
    this.releaseScreen();
  }
}

import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { EventItem, Match } from '@shared/models';
import { ApiClient, RealtimeService } from '@shared/services';
import { Subject, of } from 'rxjs';
import { PlayerPrefs } from '../core/player-prefs';
import { FRAME_STALL_MS, RECONNECT_BASE_MS, WEBRTC_FIRST_FRAME_MS, type PlaybackSource, VideoStageComponent } from './video-stage';

/** Live streams: WebRTC first, HLS fallback, stable playback through unrelated updates, recovery. */

class FakePeer {
  static created: FakePeer[] = [];
  ontrack: ((e: { streams: MediaStream[]; track: MediaStreamTrack }) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  connectionState: RTCPeerConnectionState = 'new';
  localDescription = { sdp: 'v=0 offer' };
  closed = false;
  constructor(readonly config: RTCConfiguration) {
    FakePeer.created.push(this);
  }
  addTransceiver() {}
  async createOffer() {
    return { type: 'offer', sdp: 'v=0 offer' };
  }
  async setLocalDescription() {}
  async setRemoteDescription() {}
  async getStats() {
    return new Map();
  }
  close() {
    this.closed = true;
  }
  /** The server's media arrives. */
  deliver() {
    const stream = {} as MediaStream;
    this.ontrack?.({ streams: [stream], track: {} as MediaStreamTrack });
  }
  set(state: RTCPeerConnectionState) {
    this.connectionState = state;
    this.onconnectionstatechange?.();
  }
}

const STREAM: PlaybackSource = {
  type: 'webrtc',
  url: '/stream/webrtc/live/AAAAAAAAAAAAAAAAAAAAAAAA/whep?token=t1',
  fallback_url: '/stream/hls/live/AAAAAAAAAAAAAAAAAAAAAAAA/index.m3u8?token=t1',
  key: 'stream-1',
  expires_at: new Date(Date.now() + 300_000).toISOString(),
  ice_servers: [{ urls: 'stun:stun.example.org:3478' }],
};

describe('VideoStageComponent live streams', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let statusFrames: Subject<unknown>;

  beforeEach(() => {
    FakePeer.created = [];
    statusFrames = new Subject();
    vi.stubGlobal('RTCPeerConnection', FakePeer);
    fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === 'DELETE'
        ? new Response(null, { status: 200 })
        : new Response('v=0 answer', {
            status: 201,
            headers: { Location: '/live/AAAAAAAAAAAAAAAAAAAAAAAA/whep/0f0e8c4a-7b6e-4f7c-9a55-3f2d4a1b2c3d' },
          }),
    );
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    TestBed.configureTestingModule({
      providers: [
        { provide: ApiClient, useValue: { post: vi.fn() } },
        { provide: RealtimeService, useValue: { state: signal('open'), on: (t: string) => (t === 'video.stream_status' ? statusFrames : new Subject()) } },
        { provide: PlayerPrefs, useValue: { videoMuted: () => true, videoVolume: () => 0.8, liveUpdates: () => true, update: vi.fn() } },
      ],
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function mount(source: PlaybackSource | undefined = STREAM) {
    const fixture = TestBed.createComponent(VideoStageComponent);
    if (source) fixture.componentRef.setInput('playbackSource', source);
    fixture.componentRef.setInput('match', { id: 'm1', match_number: 7, status: 'in_progress', result: null } as Match);
    fixture.detectChanges();
    const video = fixture.nativeElement.querySelector('video') as HTMLVideoElement;
    return { fixture, component: fixture.componentInstance, video };
  }

  const settle = () => new Promise((r) => setTimeout(r, 0));

  it('starts with WebRTC (WHEP) and shows the live badge once the picture arrives', async () => {
    const { fixture, component, video } = mount();
    await settle();
    expect(FakePeer.created).toHaveLength(1);
    expect(FakePeer.created[0].config.iceServers).toEqual(STREAM.ice_servers);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(STREAM.url);
    expect(init.method).toBe('POST');
    expect(init.headers['Content-Type']).toBe('application/sdp');
    FakePeer.created[0].deliver();
    video.dispatchEvent(new Event('loadeddata'));
    fixture.detectChanges();
    expect(component.transport()).toBe('webrtc');
    expect(component.live()).toBe(true);
    expect(fixture.nativeElement.querySelector('.live-badge')?.textContent).toContain('LIVE');
    expect(component.sourceLabel()).toBe('Live · WebRTC');
  });

  it('keeps playing through odds, fight, balance and realtime updates and a token refresh', async () => {
    const { fixture, video } = mount();
    await settle();
    FakePeer.created[0].deliver();
    video.dispatchEvent(new Event('loadeddata'));
    for (let n = 8; n < 12; n++) {
      fixture.componentRef.setInput('match', { id: `m${n}`, match_number: n, status: 'in_progress', result: null } as Match);
      fixture.componentRef.setInput('announcement', n % 2 ? null : { matchNumber: n, outcome: 'meron', corrected: false });
      fixture.componentRef.setInput('loading', n % 2 === 0);
      fixture.detectChanges();
    }
    // Same stream, new token: the picture must not restart.
    fixture.componentRef.setInput('playbackSource', { ...STREAM, url: STREAM.url.replace('t1', 't2'), fallback_url: STREAM.fallback_url!.replace('t1', 't2') });
    fixture.detectChanges();
    await settle();
    expect(FakePeer.created).toHaveLength(1);
    expect(FakePeer.created[0].closed).toBe(false);
  });

  it('falls back to Low-Latency HLS when the browser has no WebRTC', async () => {
    vi.stubGlobal('RTCPeerConnection', undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('maybe');
    const { component, video } = mount();
    await vi.waitFor(() => expect(video.getAttribute('src')).toBe(STREAM.fallback_url));
    expect(component.transport()).toBe('hls');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('falls back to HLS when WebRTC shows no picture in time (UDP blocked)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('maybe');
    const { component, video } = mount();
    await vi.advanceTimersByTimeAsync(0);
    expect(component.transport()).toBe('webrtc');
    await vi.advanceTimersByTimeAsync(WEBRTC_FIRST_FRAME_MS + 10);
    await vi.waitFor(() => expect(video.getAttribute('src')).toBe(STREAM.fallback_url));
    expect(component.transport()).toBe('hls');
    expect(FakePeer.created[0].closed).toBe(true);
  });

  it('releases the server session when the viewer leaves', async () => {
    const { fixture } = mount();
    await settle();
    fixture.destroy();
    expect(FakePeer.created[0].closed).toBe(true);
    const del = fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE');
    expect(del?.[0]).toBe('/stream/webrtc/live/AAAAAAAAAAAAAAAAAAAAAAAA/whep/0f0e8c4a-7b6e-4f7c-9a55-3f2d4a1b2c3d?token=t1');
  });

  it('retries an offline stream with exponential backoff and reconnects at once when it goes live', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const api = TestBed.inject(ApiClient) as unknown as { post: ReturnType<typeof vi.fn> };
    const offline = { status: 'offline', message: 'Stream offline', playback: null, stream: { id: 's1', name: 'Main', status: 'offline' } };
    api.post.mockReturnValue(of(offline));
    vi.spyOn(Math, 'random').mockReturnValue(0.5); // no jitter: exact 1 s, 2 s, 4 s
    const fixture = TestBed.createComponent(VideoStageComponent);
    fixture.componentRef.setInput('event', { id: 'e1', name: 'Derby' } as EventItem);
    fixture.componentRef.setInput('match', { id: 'm1', match_number: 1, status: 'in_progress', result: null } as Match);
    fixture.detectChanges();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(fixture.componentInstance.providerMessage()).toBe('Stream offline');
    // Attempts at +1 s, +3 s and +7 s: each wait twice the previous one.
    let elapsed = 0;
    for (const [calls, at] of [[2, RECONNECT_BASE_MS], [3, RECONNECT_BASE_MS * 3], [4, RECONNECT_BASE_MS * 7]] as const) {
      await vi.advanceTimersByTimeAsync(at - 5 - elapsed);
      expect(api.post).toHaveBeenCalledTimes(calls - 1);
      await vi.advanceTimersByTimeAsync(10);
      expect(api.post).toHaveBeenCalledTimes(calls);
      elapsed = at + 5;
    }
    // The server reports the stream live: no waiting for the 8 s step.
    statusFrames.next({ type: 'video.stream_status', data: { stream_id: 's1', status: 'live' } });
    expect(api.post).toHaveBeenCalledTimes(5);
    fixture.destroy();
  });

  function arena(api: { post: ReturnType<typeof vi.fn> }) {
    const fixture = TestBed.createComponent(VideoStageComponent);
    fixture.componentRef.setInput('event', { id: 'e1', name: 'Derby' } as EventItem);
    fixture.componentRef.setInput('match', { id: 'm1', match_number: 1, status: 'in_progress', result: null } as Match);
    fixture.detectChanges();
    void api;
    return { fixture, component: fixture.componentInstance, video: fixture.nativeElement.querySelector('video') as HTMLVideoElement };
  }
  const live = { status: 'live', message: 'Live', playback: STREAM, stream: { id: 's1', name: 'Main', status: 'live' } };
  const gone = { status: 'reconnecting', message: 'Interrupted', playback: null, stream: { id: 's1', name: 'Main', status: 'reconnecting' } };

  it('drops a dead session when the server reports the source gone, and reconnects the moment it is live', async () => {
    const api = TestBed.inject(ApiClient) as unknown as { post: ReturnType<typeof vi.fn> };
    api.post.mockReturnValue(of(live));
    const { fixture, component, video } = arena(api);
    await settle();
    FakePeer.created[0].deliver();
    video.dispatchEvent(new Event('loadeddata'));
    expect(component.live()).toBe(true);

    api.post.mockReturnValue(of(gone));
    statusFrames.next({ stream_id: 's1', status: 'reconnecting' }); // RealtimeService.on() delivers data
    expect(FakePeer.created[0].closed).toBe(true);
    expect(component.state()).toBe('error');

    api.post.mockReturnValue(of(live));
    const calls = api.post.mock.calls.length;
    statusFrames.next({ stream_id: 's1', status: 'live' });
    expect(api.post.mock.calls.length).toBe(calls + 1);
    await settle();
    expect(FakePeer.created).toHaveLength(2);
    // A status for another stream, or a server-side "error", never interrupts playback.
    FakePeer.created[1].deliver();
    video.dispatchEvent(new Event('loadeddata'));
    statusFrames.next({ stream_id: 'other', status: 'offline' });
    statusFrames.next({ stream_id: 's1', status: 'error' });
    expect(FakePeer.created[1].closed).toBe(false);
    fixture.destroy();
  });

  it('treats a stream that stops presenting frames as dead and reconnects', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const callbacks: (() => void)[] = [];
    Object.defineProperty(HTMLVideoElement.prototype, 'requestVideoFrameCallback', {
      configurable: true,
      value(cb: () => void) { callbacks.push(cb); return callbacks.length; },
    });
    vi.spyOn(HTMLMediaElement.prototype, 'paused', 'get').mockReturnValue(false); // jsdom: always paused
    try {
      const api = TestBed.inject(ApiClient) as unknown as { post: ReturnType<typeof vi.fn> };
      api.post.mockReturnValue(of(live));
      const { fixture, component, video } = arena(api);
      await vi.advanceTimersByTimeAsync(0);
      FakePeer.created[0].deliver();
      video.dispatchEvent(new Event('loadeddata'));
      // Frames keep arriving: nothing happens.
      for (let i = 0; i < 4; i++) {
        callbacks.splice(0).forEach((cb) => cb());
        await vi.advanceTimersByTimeAsync(1000);
      }
      expect(FakePeer.created[0].closed).toBe(false);
      // Frames stop (the server vanished without closing the connection).
      const requests = api.post.mock.calls.length;
      await vi.advanceTimersByTimeAsync(FRAME_STALL_MS + 1100);
      expect(FakePeer.created[0].closed).toBe(true);
      // ...and the player is already reconnecting (first backoff step is 1 s).
      await vi.advanceTimersByTimeAsync(RECONNECT_BASE_MS * 1.3);
      expect(api.post.mock.calls.length).toBeGreaterThan(requests);
      expect(['loading', 'ready']).toContain(component.state());
      fixture.destroy();
    } finally {
      delete (HTMLVideoElement.prototype as unknown as { requestVideoFrameCallback?: unknown }).requestVideoFrameCallback;
    }
  });
});

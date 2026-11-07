import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { OperatorApi } from '@shared/services';

import { Broadcaster } from './broadcaster';

/** Minimal MediaStreamTrack / MediaStream stand-ins for capture logic (no real devices). */
class FakeTrack extends EventTarget {
  readyState: 'live' | 'ended' = 'live';
  contentHint = '';
  stopped = false;
  constructor(readonly kind: 'audio' | 'video', readonly label: string, private readonly id = label) { super(); }
  getSettings() { return { deviceId: this.id, width: 1280, height: 720 }; }
  async applyConstraints() { /* accepted */ }
  stop() { this.stopped = true; this.readyState = 'ended'; }
  /** What the browser does when the user presses its own "Stop sharing". */
  end() { this.readyState = 'ended'; this.dispatchEvent(new Event('ended')); }
}
class FakeStream {
  constructor(private tracks: FakeTrack[] = []) {}
  getTracks() { return [...this.tracks]; }
  getVideoTracks() { return this.tracks.filter((t) => t.kind === 'video'); }
  getAudioTracks() { return this.tracks.filter((t) => t.kind === 'audio'); }
  addTrack(t: FakeTrack) { this.tracks.push(t); }
}

describe('Broadcaster screen sharing', () => {
  const saved = { stream: globalThis.MediaStream, nav: Object.getOwnPropertyDescriptor(globalThis.navigator, 'mediaDevices') };
  let screen: FakeTrack;
  let screenAudio: FakeTrack;
  let mic: FakeTrack;
  let camera: FakeTrack;
  let api: { stopBroadcast: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    (globalThis as Record<string, unknown>)['MediaStream'] = FakeStream;
    (globalThis as Record<string, unknown>)['RTCPeerConnection'] ??= class {};
    screen = new FakeTrack('video', 'Screen 1');
    screenAudio = new FakeTrack('audio', 'Tab audio');
    mic = new FakeTrack('audio', 'Built-in mic', 'mic-1');
    camera = new FakeTrack('video', 'FaceTime camera', 'cam-1');
    Object.defineProperty(globalThis.navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getDisplayMedia: vi.fn(async () => new FakeStream([screen, screenAudio])),
        getUserMedia: vi.fn(async (c: MediaStreamConstraints) => new FakeStream(c.video ? [camera, mic] : [mic])),
        enumerateDevices: vi.fn(async () => []),
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      },
    });
    api = { stopBroadcast: vi.fn(() => of(null)) };
    TestBed.configureTestingModule({ providers: [Broadcaster, { provide: OperatorApi, useValue: api }] });
  });
  afterEach(() => {
    (globalThis as Record<string, unknown>)['MediaStream'] = saved.stream;
    if (saved.nav) Object.defineProperty(globalThis.navigator, 'mediaDevices', saved.nav);
    else delete (globalThis.navigator as unknown as Record<string, unknown>)['mediaDevices'];
  });

  it('captures the chosen screen with the microphone, tuned for motion', async () => {
    const b = TestBed.inject(Broadcaster);
    b.source.set('screen');
    await b.openPreview();
    const media = b.preview()!;
    expect(media.getVideoTracks()[0]).toBe(screen as unknown as MediaStreamTrack);
    expect(media.getAudioTracks()[0]).toBe(mic as unknown as MediaStreamTrack);
    expect(screen.contentHint).toBe('motion');
    expect(screenAudio.stopped).toBe(true); // the microphone replaces the tab audio
    expect(b.sharing()).toBe('Screen 1');
  });

  it('falls back to the shared tab audio when the microphone is excluded', async () => {
    const b = TestBed.inject(Broadcaster);
    b.source.set('screen');
    b.includeMicrophone.set(false);
    await b.openPreview();
    expect(b.preview()!.getAudioTracks()[0]).toBe(screenAudio as unknown as MediaStreamTrack);
  });

  it('switches camera → screen on a live broadcast by replacing tracks, keeping the connection', async () => {
    const b = TestBed.inject(Broadcaster);
    await b.openPreview(); // camera
    const replaced: MediaStreamTrack[] = [];
    (b as unknown as { publisher: unknown }).publisher = {
      replaceTrack: async (t: MediaStreamTrack) => void replaced.push(t), setScaleDown: async () => undefined, close: () => undefined,
    };
    b.state.set('live');
    await b.setSource('screen');
    expect(b.source()).toBe('screen');
    expect(replaced).toContain(screen as unknown as MediaStreamTrack);
    expect(camera.stopped).toBe(true);
    expect(b.state()).toBe('live');
  });

  it('stops the broadcast when the browser’s “Stop sharing” ends the screen', async () => {
    const b = TestBed.inject(Broadcaster);
    b.source.set('screen');
    await b.openPreview();
    (b as unknown as { streamId: string }).streamId = 'stream-1';
    b.state.set('live');
    screen.end();
    await Promise.resolve();
    expect(b.state()).toBe('offline');
    expect(api.stopBroadcast).toHaveBeenCalledWith('stream-1');
    expect(b.error()).toContain('Screen sharing ended');
  });

  it('keeps the previous capture when the screen picker is cancelled', async () => {
    const b = TestBed.inject(Broadcaster);
    await b.openPreview(); // camera
    (navigator.mediaDevices.getDisplayMedia as ReturnType<typeof vi.fn>).mockRejectedValueOnce(Object.assign(new Error('x'), { name: 'NotAllowedError' }));
    await b.setSource('screen');
    expect(b.source()).toBe('camera');
    expect(b.preview()!.getVideoTracks()[0]).toBe(camera as unknown as MediaStreamTrack);
  });

  it('broadcasts a selected area live, then the whole screen again, without reconnecting', async () => {
    localStorage.removeItem('esabong.operator.screenArea');
    const b = TestBed.inject(Broadcaster);
    const cropped = new FakeTrack('video', 'cropped');
    const crops: unknown[] = [];
    let stopped = false;
    b.cropperFactory = (source, crop) => {
      expect(source).toBe(screen as unknown as MediaStreamTrack);
      crops.push(crop);
      return { track: cropped as unknown as MediaStreamTrack, method: 'frames', setCrop: (c) => void crops.push(c), outputSize: () => null, stop: () => { stopped = true; } };
    };
    b.source.set('screen');
    await b.openPreview();
    expect(b.preview()!.getVideoTracks()[0]).toBe(screen as unknown as MediaStreamTrack); // whole screen: no cropping
    const replaced: MediaStreamTrack[] = [];
    const scales: number[] = [];
    (b as unknown as { publisher: unknown }).publisher = {
      replaceTrack: async (t: MediaStreamTrack) => void replaced.push(t), setScaleDown: async (f: number) => void scales.push(f), close: () => undefined,
    };
    b.state.set('live');

    await b.setArea({ x: 0, y: 0, w: 0.5, h: 0.5 });
    expect(b.preview()!.getVideoTracks()[0]).toBe(cropped as unknown as MediaStreamTrack);
    expect(replaced.at(-1)).toBe(cropped as unknown as MediaStreamTrack);
    expect(b.preview()!.getAudioTracks()[0]).toBe(mic as unknown as MediaStreamTrack); // sound kept
    expect(b.captured()).toBe('640×360 area');
    expect(scales.at(-1)).toBe(1); // 360 px tall area fits 720p

    await b.setArea({ x: 0.5, y: 0.5, w: 0.5, h: 0.5 }); // move: same track, new crop
    expect(crops.at(-1)).toEqual({ x: 0.5, y: 0.5, w: 0.5, h: 0.5 });
    expect(replaced.filter((t) => t === (cropped as unknown as MediaStreamTrack)).length).toBe(1);

    await b.setArea(null);
    expect(b.preview()!.getVideoTracks()[0]).toBe(screen as unknown as MediaStreamTrack);
    expect(replaced.at(-1)).toBe(screen as unknown as MediaStreamTrack);
    expect(stopped).toBe(true);
    expect(screen.stopped).toBe(false); // the shared screen keeps running
    expect(b.state()).toBe('live');
    expect(localStorage.getItem('esabong.operator.screenArea')).toBeNull();
  });
});

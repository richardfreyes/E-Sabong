import { WhipError, WhipPublisher, preferH264 } from './whip-publisher';
import { formatBytes, formatDuration } from './recording-player';
import { mediaErrorMessage } from './broadcaster';

class FakeSender {
  params: RTCRtpSendParameters = { encodings: [{}] } as unknown as RTCRtpSendParameters;
  track: MediaStreamTrack | null = null;
  getParameters() { return this.params; }
  async setParameters(p: RTCRtpSendParameters) { this.params = p; }
  async replaceTrack(t: MediaStreamTrack) { this.track = t; }
}
class FakeTransceiver {
  sender = new FakeSender();
  prefs: RTCRtpCodec[] = [];
  setCodecPreferences(c: RTCRtpCodec[]) { this.prefs = c; }
}
class FakePeer {
  static last: FakePeer;
  transceivers: FakeTransceiver[] = [];
  localDescription: RTCSessionDescriptionInit | null = null;
  remote: RTCSessionDescriptionInit | null = null;
  connectionState: RTCPeerConnectionState = 'new';
  onconnectionstatechange: (() => void) | null = null;
  closed = false;
  constructor() { FakePeer.last = this; }
  addTransceiver(track: MediaStreamTrack) { const t = new FakeTransceiver(); t.sender.track = track; this.transceivers.push(t); return t; }
  async createOffer() { return { type: 'offer', sdp: 'v=0 offer' }; }
  async setLocalDescription(d: RTCSessionDescriptionInit) { this.localDescription = d; }
  async setRemoteDescription(d: RTCSessionDescriptionInit) { this.remote = d; }
  getSenders() { return this.transceivers.map((t) => t.sender); }
  async getStats() { return new Map(); }
  close() { this.closed = true; }
}

const CODECS = [
  { mimeType: 'video/VP8', clockRate: 90000 },
  { mimeType: 'video/H264', clockRate: 90000, sdpFmtpLine: 'packetization-mode=0;profile-level-id=42e01f' },
  { mimeType: 'video/H264', clockRate: 90000, sdpFmtpLine: 'packetization-mode=1;profile-level-id=42e01f' },
];

describe('WhipPublisher', () => {
  const original = { pc: globalThis.RTCPeerConnection, sender: globalThis.RTCRtpSender, fetch: globalThis.fetch };
  let calls: { url: string; init: RequestInit }[];

  beforeEach(() => {
    calls = [];
    (globalThis as Record<string, unknown>)['RTCPeerConnection'] = FakePeer;
    (globalThis as Record<string, unknown>)['RTCRtpSender'] = { getCapabilities: () => ({ codecs: CODECS, headerExtensions: [] }) };
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (init.method === 'DELETE') return new Response(null, { status: 200 });
      return new Response('v=0 answer', { status: 201, headers: { Location: '/live/abc/whip/0f6b9a3e-1111-4222-8333-944455556666' } });
    }) as typeof fetch;
  });
  afterEach(() => {
    (globalThis as Record<string, unknown>)['RTCPeerConnection'] = original.pc;
    (globalThis as Record<string, unknown>)['RTCRtpSender'] = original.sender;
    globalThis.fetch = original.fetch;
  });

  function media(): MediaStream {
    const tracks = [{ kind: 'video' }, { kind: 'audio' }] as MediaStreamTrack[];
    return { getTracks: () => tracks } as unknown as MediaStream;
  }

  it('posts the offer to the tokenized URL, caps the bitrate, prefers H.264 and deletes the session on close', async () => {
    const url = '/stream/webrtc/live/abcdefghijklmnopqrstuvwx/whip?token=t.s';
    const pub = new WhipPublisher(url, [], { state: () => undefined });
    await pub.start(media(), 2500);
    expect(calls[0].url).toBe(url);
    expect(calls[0].init.method).toBe('POST');
    expect((calls[0].init.headers as Record<string, string>)['Content-Type']).toBe('application/sdp');
    const peer = FakePeer.last;
    expect(peer.remote?.sdp).toBe('v=0 answer');
    const video = peer.transceivers[0];
    expect(video.sender.params.encodings[0].maxBitrate).toBe(2_500_000);
    expect(video.prefs.slice(0, 2).map((c) => c.sdpFmtpLine)).toEqual([CODECS[2].sdpFmtpLine, CODECS[1].sdpFmtpLine]);
    expect(video.prefs.at(-1)?.mimeType).toBe('video/VP8');
    pub.close();
    expect(peer.closed).toBe(true);
    expect(calls[1]).toEqual({
      url: '/stream/webrtc/live/abcdefghijklmnopqrstuvwx/whip/0f6b9a3e-1111-4222-8333-944455556666?token=t.s',
      init: expect.objectContaining({ method: 'DELETE' }),
    });
  });

  it('reports a refused publish as a WhipError', async () => {
    globalThis.fetch = (async () => new Response('', { status: 401 })) as typeof fetch;
    const pub = new WhipPublisher('/stream/webrtc/live/x/whip?token=bad', [], { state: () => undefined });
    await expect(pub.start(media(), 2500)).rejects.toBeInstanceOf(WhipError);
  });

  it('keeps browser defaults when H.264 is unavailable', () => {
    (globalThis as Record<string, unknown>)['RTCRtpSender'] = { getCapabilities: () => ({ codecs: [CODECS[0]], headerExtensions: [] }) };
    const t = new FakeTransceiver();
    preferH264(t as unknown as RTCRtpTransceiver);
    expect(t.prefs).toEqual([]);
  });
});

describe('Operator console helpers', () => {
  it('formats durations and sizes', () => {
    expect(formatDuration(65)).toBe('01:05');
    expect(formatDuration(3725)).toBe('1:02:05');
    expect(formatDuration(null)).toBe('—');
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1536)).toBe('2 KB');
    expect(formatBytes(5 * 1024 ** 3)).toBe('5.0 GB');
  });

  it('explains camera and microphone failures', () => {
    expect(mediaErrorMessage({ name: 'NotAllowedError' })).toContain('blocked');
    expect(mediaErrorMessage({ name: 'NotReadableError' })).toContain('in use');
    expect(mediaErrorMessage({ name: 'NotFoundError' })).toContain('not found');
    expect(mediaErrorMessage(new WhipError(401))).toContain('refused');
  });
});

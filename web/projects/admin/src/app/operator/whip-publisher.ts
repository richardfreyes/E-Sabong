/**
 * WHIP (WebRTC-HTTP Ingestion Protocol) publisher for MediaMTX.
 *
 * Sends the operator's camera and microphone to `live/<id>` through the same-origin
 * `/stream/webrtc/` proxy. H.264 is preferred so the stream also plays over LL-HLS and records
 * as MP4; Opus audio plays over WebRTC without the server-side relay. The publish URL carries a
 * short-lived token bound to this broadcast session; no long-lived key reaches the browser.
 */
export class WhipError extends Error {
  constructor(readonly status: number) {
    super(status === 401 ? 'The streaming server refused this broadcast (session ended or not authorized)' : `WHIP request failed (${status})`);
  }
}

export interface PublishStats {
  /** Encoder output, kbit/s. */
  bitrateKbps: number | null;
  fps: number | null;
  width: number | null;
  height: number | null;
  /** Round-trip time to the server, ms. */
  rttMs: number | null;
  /** Share of packets the server reported lost, 0-100. */
  lossPercent: number | null;
  /** Why the encoder is lowering quality ('bandwidth', 'cpu') or null. */
  limitation: string | null;
}

export interface WhipCallbacks {
  state(state: RTCPeerConnectionState): void;
}

export class WhipPublisher {
  private pc: RTCPeerConnection | null = null;
  private resource: string | null = null;
  private closed = false;
  private lastBytes: { bytes: number; at: number } | null = null;

  constructor(
    private readonly url: string,
    private readonly iceServers: RTCIceServer[],
    private readonly on: WhipCallbacks,
  ) {}

  static supported(): boolean {
    return typeof RTCPeerConnection !== 'undefined' && typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;
  }

  async start(stream: MediaStream, maxBitrateKbps: number, signal?: AbortSignal): Promise<void> {
    const pc = new RTCPeerConnection({ iceServers: this.iceServers, bundlePolicy: 'max-bundle' });
    this.pc = pc;
    pc.onconnectionstatechange = () => {
      if (!this.closed) this.on.state(pc.connectionState);
    };
    for (const track of stream.getTracks()) {
      const transceiver = pc.addTransceiver(track, { direction: 'sendonly', streams: [stream] });
      if (track.kind === 'video') {
        preferH264(transceiver);
        const params = transceiver.sender.getParameters();
        params.encodings = [{ ...(params.encodings?.[0] ?? {}), maxBitrate: maxBitrateKbps * 1000 }];
        params.degradationPreference = 'maintain-framerate';
        await transceiver.sender.setParameters(params).catch(() => undefined);
      }
    }
    await pc.setLocalDescription(await pc.createOffer());
    const response = await fetch(this.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: pc.localDescription?.sdp ?? '',
      credentials: 'same-origin',
      signal,
    });
    if (response.status !== 201) throw new WhipError(response.status);
    this.resource = this.resourceUrl(response.headers.get('Location'));
    const answer = await response.text();
    if (this.closed) return;
    await pc.setRemoteDescription({ type: 'answer', sdp: answer });
  }

  /** Swap the camera or microphone without renegotiating (no interruption for viewers). */
  async replaceTrack(track: MediaStreamTrack): Promise<void> {
    const sender = this.pc?.getSenders().find((s) => s.track?.kind === track.kind);
    await sender?.replaceTrack(track);
  }

  /** Encode the video at 1/``factor`` of its size (1 = as captured); bitrate stays capped. */
  async setScaleDown(factor: number): Promise<void> {
    const sender = this.pc?.getSenders().find((s) => s.track?.kind === 'video');
    if (!sender) return;
    const params = sender.getParameters();
    if (!params.encodings?.length) return;
    const value = Math.max(1, Math.round(factor * 100) / 100);
    if (params.encodings[0].scaleResolutionDownBy === value) return;
    params.encodings[0].scaleResolutionDownBy = value;
    await sender.setParameters(params).catch(() => undefined);
  }

  async stats(): Promise<PublishStats> {
    const out: PublishStats = { bitrateKbps: null, fps: null, width: null, height: null, rttMs: null, lossPercent: null, limitation: null };
    if (!this.pc) return out;
    const report = await this.pc.getStats();
    let bytes = 0;
    report.forEach((s: RTCStats & Record<string, unknown>) => {
      if (s.type === 'outbound-rtp') {
        bytes += Number(s['bytesSent'] ?? 0);
        if (s['kind'] === 'video') {
          out.fps = numberOrNull(s['framesPerSecond']);
          out.width = numberOrNull(s['frameWidth']);
          out.height = numberOrNull(s['frameHeight']);
          const limit = s['qualityLimitationReason'];
          out.limitation = typeof limit === 'string' && limit !== 'none' ? limit : null;
        }
      } else if (s.type === 'remote-inbound-rtp' && s['kind'] === 'video') {
        const rtt = numberOrNull(s['roundTripTime']);
        out.rttMs = rtt === null ? null : Math.round(rtt * 1000);
        const lost = numberOrNull(s['fractionLost']);
        out.lossPercent = lost === null ? null : Math.round(lost * 1000) / 10;
      } else if (s.type === 'candidate-pair' && s['nominated'] && out.rttMs === null) {
        const rtt = numberOrNull(s['currentRoundTripTime']);
        out.rttMs = rtt === null ? null : Math.round(rtt * 1000);
      }
    });
    const now = performance.now();
    if (this.lastBytes && now > this.lastBytes.at && bytes >= this.lastBytes.bytes) {
      out.bitrateKbps = Math.round(((bytes - this.lastBytes.bytes) * 8) / (now - this.lastBytes.at));
    }
    this.lastBytes = { bytes, at: now };
    return out;
  }

  /** Stops publishing and releases the server session (best effort, survives page unload). */
  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.pc?.close();
    this.pc = null;
    if (this.resource) {
      void fetch(this.resource, { method: 'DELETE', keepalive: true, credentials: 'same-origin' }).catch(() => undefined);
    }
  }

  /** MediaMTX answers Location: /live/<id>/whip/<session>; rebuild it under the proxy prefix. */
  private resourceUrl(location: string | null): string | null {
    const session = location?.split('/').pop();
    if (!session || !/^[0-9a-f-]{36}$/i.test(session)) return null;
    const base = new URL(this.url, typeof document === 'undefined' ? 'http://localhost' : document.baseURI);
    base.pathname = `${base.pathname.replace(/\/$/, '')}/${session}`;
    return base.pathname + base.search;
  }
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Put H.264 first (keeps other codecs as fallback when the browser has no H.264 encoder). */
export function preferH264(transceiver: RTCRtpTransceiver): void {
  const caps = typeof RTCRtpSender !== 'undefined' && RTCRtpSender.getCapabilities ? RTCRtpSender.getCapabilities('video') : null;
  if (!caps || typeof transceiver.setCodecPreferences !== 'function') return;
  const h264 = caps.codecs.filter((c) => c.mimeType.toLowerCase() === 'video/h264');
  if (!h264.length) return;
  // Constrained baseline / packetization-mode=1 first: the widest decoder support.
  h264.sort((a, b) => score(b) - score(a));
  const rest = caps.codecs.filter((c) => c.mimeType.toLowerCase() !== 'video/h264');
  try {
    transceiver.setCodecPreferences([...h264, ...rest]);
  } catch {
    /* keep browser defaults */
  }
}

function score(codec: RTCRtpCodec): number {
  const fmtp = codec.sdpFmtpLine ?? '';
  return (fmtp.includes('packetization-mode=1') ? 2 : 0) + (fmtp.includes('profile-level-id=42e0') ? 1 : 0);
}

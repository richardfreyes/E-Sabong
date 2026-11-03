/**
 * Minimal WHEP (WebRTC-HTTP Egress Protocol) viewer for MediaMTX.
 *
 * The offer is sent immediately, without waiting for ICE gathering: MediaMTX answers with its
 * public candidate and learns the browser's address from the connectivity checks, which keeps
 * start-up to one HTTP round trip. Closing deletes the server session so a viewer that leaves
 * stops consuming server bandwidth at once.
 */
export class WhepError extends Error {
  constructor(readonly status: number) {
    super(`WHEP request failed (${status})`);
  }
}

export interface WhepCallbacks {
  track(stream: MediaStream): void;
  state(state: RTCPeerConnectionState): void;
}

export class WhepSession {
  private pc: RTCPeerConnection | null = null;
  private resource: string | null = null;
  private closed = false;

  constructor(
    private readonly url: string,
    private readonly iceServers: RTCIceServer[],
    private readonly on: WhepCallbacks,
  ) {}

  static supported(): boolean {
    return typeof RTCPeerConnection !== 'undefined';
  }

  async start(signal?: AbortSignal): Promise<void> {
    const pc = new RTCPeerConnection({ iceServers: this.iceServers, bundlePolicy: 'max-bundle' });
    this.pc = pc;
    pc.addTransceiver('video', { direction: 'recvonly' });
    pc.addTransceiver('audio', { direction: 'recvonly' });
    pc.ontrack = (event) => {
      if (!this.closed) this.on.track(event.streams[0] ?? new MediaStream([event.track]));
    };
    pc.onconnectionstatechange = () => {
      if (!this.closed) this.on.state(pc.connectionState);
    };
    await pc.setLocalDescription(await pc.createOffer());
    const response = await fetch(this.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: pc.localDescription?.sdp ?? '',
      credentials: 'same-origin',
      signal,
    });
    if (response.status !== 201) throw new WhepError(response.status);
    this.resource = this.resourceUrl(response.headers.get('Location'));
    const answer = await response.text();
    if (this.closed) return;
    await pc.setRemoteDescription({ type: 'answer', sdp: answer });
  }

  /** Stops playback and releases the server session (best effort, survives page unload). */
  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.pc?.close();
    this.pc = null;
    if (this.resource) {
      void fetch(this.resource, { method: 'DELETE', keepalive: true, credentials: 'same-origin' }).catch(() => undefined);
    }
  }

  async stats(): Promise<RTCStatsReport | null> {
    return this.pc ? this.pc.getStats() : null;
  }

  /**
   * MediaMTX answers with Location: /live/<id>/whep/<session>, relative to its own root. The
   * browser reaches it through the /stream/webrtc proxy, so rebuild the URL from the request.
   */
  private resourceUrl(location: string | null): string | null {
    const session = location?.split('/').pop();
    if (!session || !/^[0-9a-f-]{36}$/i.test(session)) return null;
    const base = new URL(this.url, typeof document === 'undefined' ? 'http://localhost' : document.baseURI);
    base.pathname = `${base.pathname.replace(/\/$/, '')}/${session}`;
    return base.pathname + base.search;
  }
}

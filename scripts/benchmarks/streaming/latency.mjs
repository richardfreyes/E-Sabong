// Glass-to-glass latency: the publisher paints Date.now() into every frame (44 black/white
// blocks) and publishes over WHIP; viewers decode the blocks from each displayed frame. One
// browser, one clock: latency = display time - paint time. Excludes camera exposure and
// monitor scan-out (both outside this system).
// Run from the repository root: node scripts/benchmarks/streaming/<script>.mjs …
const { chromium } = await import(new URL('../../../web/node_modules/playwright/index.mjs', import.meta.url).href);
import { execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
const [, , slug, outFile] = process.argv;
const API = process.env.BENCH_API_URL ?? 'http://127.0.0.1:8016/api/v1', MTX = process.env.BENCH_WEBRTC_URL ?? 'http://127.0.0.1:18889', HLS = process.env.BENCH_HLS_URL ?? 'http://127.0.0.1:18888';
const login = await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'X-Tenant-Slug': slug, 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'admin', password: 'DemoPass#2026' }) })).json();
const H = { 'X-Tenant-Slug': slug, 'X-Requested-With': 'XMLHttpRequest', Authorization: `Bearer ${login.access_token}`, 'Content-Type': 'application/json' };
const api = async (m, p, b) => (await fetch(`${API}${p}`, { method: m, headers: H, body: b ? JSON.stringify(b) : undefined })).json();
for (const s of await api('GET', '/admin/streams')) if (s.name.startsWith('bench-')) await fetch(`${API}/admin/streams/${s.id}`, { method: 'DELETE', headers: H });
const A = await api('POST', '/admin/streams', { name: 'bench-whip', source_kind: 'obs' });
const B = await api('POST', '/admin/streams', { name: 'bench-rtmp', source_kind: 'obs' });
const pathA = A.obs_stream_key.split('?')[0], pathB = B.obs_stream_key.split('?')[0];

const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
const pub = await browser.newPage(); await pub.goto(`${MTX}/`);
await pub.evaluate(async ({ path, key }) => {
  const c = document.createElement('canvas'); c.width = 1280; c.height = 720; document.body.append(c);
  const g = c.getContext('2d'); let n = 0;
  const draw = () => {
    const t = Date.now(); n++;
    g.fillStyle = '#336'; g.fillRect(0, 0, 1280, 720);
    g.fillStyle = '#c84'; g.fillRect((n * 7) % 1180, 300 + 120 * Math.sin(n / 15), 100, 100); // motion for the encoder
    for (let i = 0; i < 44; i++) { g.fillStyle = Math.floor(t / 2 ** i) % 2 ? '#fff' : '#000'; g.fillRect(40 + (i % 22) * 54, 40 + Math.floor(i / 22) * 54, 48, 48); }
    requestAnimationFrame(draw);
  };
  draw();
  const stream = c.captureStream(30);
  const pc = new RTCPeerConnection();
  const tr = pc.addTransceiver(stream.getVideoTracks()[0], { direction: 'sendonly' });
  const h264 = RTCRtpSender.getCapabilities('video').codecs.filter((x) => x.mimeType === 'video/H264');
  tr.setCodecPreferences(h264);
  const params = tr.sender.getParameters(); params.encodings = [{ maxBitrate: 2_500_000, maxFramerate: 30 }]; await tr.sender.setParameters(params).catch(() => {});
  await pc.setLocalDescription(await pc.createOffer());
  const r = await fetch(`/live/${path}/whip`, { method: 'POST', headers: { 'Content-Type': 'application/sdp', Authorization: `Bearer ${key}` }, body: pc.localDescription.sdp });
  if (r.status !== 201) throw new Error('WHIP ' + r.status + ' ' + (await r.text()));
  await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() });
  window.pubPc = pc;
}, { path: pathA, key: A.publish_key });
await new Promise((r) => setTimeout(r, 3000));
// OBS-like path: decode and re-encode with x264 (zerolatency, 1 s GOP) and push over RTMP with B's key.
const ff = spawn('docker', ['exec', (process.env.BENCH_MEDIAMTX_CONTAINER ?? 'esabong-e2e-mediamtx'), 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-rtsp_transport', 'tcp', '-i', `rtsp://127.0.0.1:8554/live/${pathA}`,
  '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-g', '30', '-b:v', '2500k', '-f', 'flv', `rtmp://127.0.0.1:1935/live/${B.obs_stream_key}`], { stdio: 'ignore' });
await new Promise((r) => setTimeout(r, 4000));

async function measure(label, path, transport, seconds = 20) {
  const pb = (await api('POST', `/admin/streams/${path === pathA ? A.id : B.id}/preview`)).playback;
  const url = transport === 'hls' ? pb.fallback_url.replace('/stream/hls', HLS) : (label.includes('relay') ? pb.url.replace(/\/whep/, '/whep').replace(`/live/${path}/whep`, `/live/${path}/rtc/whep`) : pb.url).replace('/stream/webrtc', MTX);
  const page = await browser.newPage(); await page.goto(transport === 'hls' ? `${HLS}/` : `${MTX}/`); // same origin as the media, like production behind Caddy
  if (transport === 'hls') await page.addScriptTag({ path: new URL('../../../web/node_modules/hls.js/dist/hls.min.js', import.meta.url).pathname });
  const out = await page.evaluate(async ({ url, transport, seconds }) => {
    const v = document.createElement('video'); v.muted = true; v.playsInline = true; document.body.append(v);
    const c = document.createElement('canvas'); c.width = 1280; c.height = 720; const g = c.getContext('2d', { willReadFrequently: true });
    const t0 = performance.now(); let first = null; let pc;
    if (transport === 'webrtc') {
      pc = new RTCPeerConnection(); pc.addTransceiver('video', { direction: 'recvonly' }); pc.addTransceiver('audio', { direction: 'recvonly' });
      pc.ontrack = (e) => { v.srcObject = e.streams[0]; v.play(); };
      await pc.setLocalDescription(await pc.createOffer());
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/sdp' }, body: pc.localDescription.sdp });
      await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() });
    } else {
      const hls = new Hls({ lowLatencyMode: true, backBufferLength: 0, liveSyncDurationCount: 1, maxLiveSyncPlaybackRate: 1.5 });
      hls.loadSource(url); hls.attachMedia(v); v.play();
    }
    const samples = []; let stalls = 0, last = performance.now();
    await new Promise((done) => {
      const end = performance.now() + seconds * 1000;
      const cb = () => {
        const now = Date.now(); const p = performance.now();
        if (first === null) first = p - t0;
        if (p - last > 250) stalls++; last = p;
        g.drawImage(v, 0, 0, 1280, 720);
        let t = 0; for (let i = 0; i < 44; i++) { const d = g.getImageData(40 + (i % 22) * 54 + 24, 40 + Math.floor(i / 22) * 54 + 24, 1, 1).data; if (d[0] + d[1] + d[2] > 384) t += 2 ** i; }
        const lat = now - t; if (lat > 0 && lat < 20000) samples.push(lat);
        if (p < end) v.requestVideoFrameCallback(cb); else done();
      };
      v.requestVideoFrameCallback(cb);
      setTimeout(done, seconds * 1000 + 15000); // never hang when no frame arrives
    });
    let loss = null, jitter = null, decoded = null;
    if (pc) (await pc.getStats()).forEach((s) => { if (s.type === 'inbound-rtp' && s.kind === 'video') { loss = s.packetsLost; jitter = s.jitter; decoded = s.framesDecoded; } });
    pc?.close();
    return { first, samples, stalls, loss, jitter, decoded, frames: samples.length };
  }, { url, transport, seconds });
  await page.close();
  const s = out.samples.sort((a, b) => a - b), q = (p) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  const row = { label, frames: out.frames, startupMs: Math.round(out.first), p50: q(0.5), p95: q(0.95), max: s[s.length - 1], stallsOver250ms: out.stalls, packetsLost: out.loss, jitterMs: out.jitter === null ? null : +(out.jitter * 1000).toFixed(1) };
  console.log(JSON.stringify(row)); fs.appendFileSync(outFile + '.log', JSON.stringify(row) + '\n'); return row;
}
const results = [];
results.push(await measure('WHIP in -> WebRTC out', pathA, 'webrtc'));
results.push(await measure('WHIP in -> WebRTC out (Opus relay path)', pathA, 'webrtc'));
results.push(await measure('WHIP in -> LL-HLS out', pathA, 'hls', 25));
results.push(await measure('x264 re-encode + RTMP in (OBS-like) -> WebRTC out', pathB, 'webrtc'));
results.push(await measure('x264 re-encode + RTMP in (OBS-like) -> LL-HLS out', pathB, 'hls', 25));
fs.writeFileSync(outFile, JSON.stringify({ when: new Date().toISOString(), results }, null, 1));
ff.kill(); await browser.close();
for (const s of [A, B]) await fetch(`${API}/admin/streams/${s.id}`, { method: 'DELETE', headers: H });

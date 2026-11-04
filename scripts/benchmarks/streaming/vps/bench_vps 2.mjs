// VPS-shaped streaming benchmark: MediaMTX limited to 2 CPU / 2 GiB with a 300 Mbit/s egress cap,
// OBS-like RTMP feed (x264 veryfast/zerolatency 2.5 Mbps + AAC 128 kbps, 1 s keyframes) from a
// separate origin, N pion WebRTC viewers (no decoding) on the same Docker network, and one
// Chromium viewer measuring glass-to-glass latency during each hold.
import { execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
// Run from the repository root: node scripts/benchmarks/streaming/vps/bench_vps.mjs <tenant-slug> <out.jsonl> 5 10 25 50 100
// Setup (isolated stack, never production): scripts/benchmarks/streaming/vps/README.md
const { chromium } = await import(new URL('../../../../web/node_modules/playwright/index.mjs', import.meta.url).href);
const [, , slug, outFile, ...levels] = process.argv;
const API = process.env.BENCH_API_URL ?? 'http://127.0.0.1:8016/api/v1';
const BITRATE = process.env.BITRATE ?? '2500k';
const login = await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'X-Tenant-Slug': slug, 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'admin', password: 'DemoPass#2026' }) })).json();
const H = { 'X-Tenant-Slug': slug, 'X-Requested-With': 'XMLHttpRequest', Authorization: `Bearer ${login.access_token}`, 'Content-Type': 'application/json' };
const api = async (m, p, b) => (await fetch(`${API}${p}`, { method: m, headers: H, body: b ? JSON.stringify(b) : undefined })).json();
for (const s of await api('GET', '/admin/streams')) if (s.name.startsWith('vps-')) await fetch(`${API}/admin/streams/${s.id}`, { method: 'DELETE', headers: H });
const A = await api('POST', '/admin/streams', { name: 'vps-origin', source_kind: 'obs' });
const B = await api('POST', '/admin/streams', { name: 'vps-main', source_kind: 'obs', enabled: true });
const pathA = A.obs_stream_key.split('?')[0];
const sh = (cmd, args) => execFileSync(cmd, args).toString().trim();

const browser = await chromium.launch();
const pub = await browser.newPage(); await pub.goto('http://127.0.0.1:28889/');
await pub.evaluate(async ({ path, key }) => {
  const c = document.createElement('canvas'); c.width = 1280; c.height = 720; document.body.append(c);
  const g = c.getContext('2d'); let n = 0;
  const draw = () => { const t = Date.now(); n++; g.fillStyle = '#2a3550'; g.fillRect(0, 0, 1280, 720);
    g.fillStyle = '#d09040'; g.fillRect((n * 9) % 1180, 320 + 140 * Math.sin(n / 13), 120, 120);
    g.fillStyle = '#4a8'; g.fillRect(200 + 300 * Math.cos(n / 21), 500, 160, 90);
    for (let i = 0; i < 44; i++) { g.fillStyle = Math.floor(t / 2 ** i) % 2 ? '#fff' : '#000'; g.fillRect(40 + (i % 22) * 54, 40 + Math.floor(i / 22) * 54, 48, 48); }
    requestAnimationFrame(draw); };
  draw();
  const pc = new RTCPeerConnection();
  const tr = pc.addTransceiver(c.captureStream(30).getVideoTracks()[0], { direction: 'sendonly' });
  tr.setCodecPreferences(RTCRtpSender.getCapabilities('video').codecs.filter((x) => x.mimeType === 'video/H264'));
  const p = tr.sender.getParameters(); p.encodings = [{ maxBitrate: 4_000_000, maxFramerate: 30 }]; await tr.sender.setParameters(p).catch(() => {});
  await pc.setLocalDescription(await pc.createOffer());
  const r = await fetch(`/live/${path}/whip`, { method: 'POST', headers: { 'Content-Type': 'application/sdp', Authorization: `Bearer ${key}` }, body: pc.localDescription.sdp });
  if (r.status !== 201) throw new Error('WHIP ' + r.status);
  await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() }); window.pc = pc;
}, { path: pathA, key: A.publish_key });
await new Promise((r) => setTimeout(r, 3000));
// OBS stand-in: re-encode with OBS-like settings and push RTMP with B's key to the server under test.
const ff = spawn('docker', ['exec', 'esabong-bench-origin', 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-rtsp_transport', 'tcp', '-i', `rtsp://127.0.0.1:8554/live/${pathA}`,
  '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-map', '0:v', '-map', '1:a', '-vf', 'noise=alls=14:allf=t+u',
  '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-g', '30', '-keyint_min', '30', '-b:v', BITRATE, '-minrate', BITRATE, '-maxrate', BITRATE, '-bufsize', String(parseInt(BITRATE) / 2) + 'k', '-x264-params', 'nal-hrd=cbr:force-cfr=1',
  '-c:a', 'aac', '-b:a', '128k', '-f', 'flv', `rtmp://esabong-bench-mediamtx:1935/live/${B.obs_stream_key}`], { stdio: 'ignore' });
await new Promise((r) => setTimeout(r, 6000));

const tx = () => Number(sh('docker', ['exec', 'esabong-bench-mediamtx', 'cat', '/sys/class/net/eth0/statistics/tx_bytes']));
const stats = () => { const [cpu, mem] = sh('docker', ['stats', '--no-stream', '--format', '{{.CPUPerc}}|{{.MemUsage}}', 'esabong-bench-mediamtx']).split('|');
  const m = mem.split('/')[0].trim(); return { cpu: parseFloat(cpu), memMiB: parseFloat(m) * (m.endsWith('GiB') ? 1024 : m.endsWith('KiB') ? 1 / 1024 : 1) }; };

async function latency(seconds) {
  const pb = (await api('POST', `/admin/streams/${B.id}/preview`)).playback;
  const page = await browser.newPage(); await page.goto('http://127.0.0.1:18889/');
  const out = await page.evaluate(async ({ url, seconds }) => {
    const v = document.createElement('video'); v.muted = true; document.body.append(v);
    const c = document.createElement('canvas'); c.width = 1280; c.height = 720; const g = c.getContext('2d', { willReadFrequently: true });
    const pc = new RTCPeerConnection(); pc.addTransceiver('video', { direction: 'recvonly' }); pc.addTransceiver('audio', { direction: 'recvonly' });
    pc.ontrack = (e) => { v.srcObject = e.streams[0]; v.play(); };
    await pc.setLocalDescription(await pc.createOffer());
    const t0 = performance.now();
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/sdp' }, body: pc.localDescription.sdp });
    await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() });
    const samples = []; let first = null;
    await new Promise((done) => { const end = performance.now() + seconds * 1000;
      const cb = () => { const now = Date.now(); if (first === null) first = performance.now() - t0; g.drawImage(v, 0, 0, 1280, 720);
        let t = 0; for (let i = 0; i < 44; i++) { const d = g.getImageData(40 + (i % 22) * 54 + 24, 40 + Math.floor(i / 22) * 54 + 24, 1, 1).data; if (d[0] + d[1] + d[2] > 384) t += 2 ** i; }
        const l = now - t; if (l > 0 && l < 20000) samples.push(l); if (performance.now() < end) v.requestVideoFrameCallback(cb); else done(); };
      v.requestVideoFrameCallback(cb); setTimeout(done, seconds * 1000 + 15000); });
    let lost = 0, recv = 0; (await pc.getStats()).forEach((s) => { if (s.type === 'inbound-rtp' && s.kind === 'video') { lost = s.packetsLost; recv = s.packetsReceived; } });
    pc.close(); return { samples, first, lost, recv };
  }, { url: pb.url.replace('/stream/webrtc', 'http://127.0.0.1:18889'), seconds });
  await page.close();
  const s = out.samples.sort((a, b) => a - b), q = (p) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  return { frames: s.length, startupMs: Math.round(out.first ?? -1), p50: q(0.5), p95: q(0.95), max: s.at(-1), chromeLossPct: +(100 * out.lost / Math.max(1, out.lost + out.recv)).toFixed(2) };
}

for (const n of levels.map(Number)) {
  const pb = (await api('POST', `/admin/streams/${B.id}/preview`)).playback;
  const url = pb.url.replace('/stream/webrtc', 'http://esabong-bench-mediamtx:8889');
  const hold = 60;
  const load = spawn('docker', ['run', '--rm', '--network', 'esabong-bench-net', 'esabong-bench-whepload', '-url', url, '-n', String(n), '-hold', `${hold}s`, '-gap', '30ms']);
  let loadOut = ''; load.stdout.on('data', (d) => (loadOut += d)); load.stderr.on('data', () => {});
  const joinWait = n * 0.03 + 8; await new Promise((r) => setTimeout(r, joinWait * 1000));
  const tx0 = tx(), t0 = Date.now(), samples = [];
  const sampler = setInterval(() => samples.push(stats()), 5000);
  const lat = await latency(40);
  await new Promise((r) => load.on('close', r));
  clearInterval(sampler);
  const txMbps = +(((tx() - tx0) * 8) / ((Date.now() - t0) / 1000) / 1e6).toFixed(1);
  const pion = JSON.parse(loadOut.trim().split('\n').at(-1));
  const row = { viewers: n, videoBitrate: BITRATE, ...pion, serverTxMbps: txMbps, cpuPctAvg: +(samples.reduce((a, s) => a + s.cpu, 0) / samples.length).toFixed(1), cpuPctMax: Math.max(...samples.map((s) => s.cpu)),
    memMiBMax: Math.round(Math.max(...samples.map((s) => s.memMiB))), latency: lat };
  console.log(JSON.stringify(row)); fs.appendFileSync(outFile, JSON.stringify(row) + '\n');
  await new Promise((r) => setTimeout(r, 10000));
}
ff.kill(); await browser.close();

// Concurrent WebRTC viewers against MediaMTX. Viewers have no <video> sink (Chromium then skips
// decoding; checked via framesDecoded), so the laptop measures the server, not 1,000 decoders.
// Every viewer still negotiates WHEP (one auth callback each), receives all RTP and reports loss.
// Run from the repository root: node scripts/benchmarks/streaming/<script>.mjs …
const { chromium } = await import(new URL('../../../web/node_modules/playwright/index.mjs', import.meta.url).href);
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
const [, , slug, streamId, outFile, ...levels] = process.argv;
const API = process.env.BENCH_API_URL ?? 'http://127.0.0.1:8016/api/v1', MTX = process.env.BENCH_WEBRTC_URL ?? 'http://127.0.0.1:18889';
const password = JSON.parse(fs.readFileSync(process.env.ENV_JSON)).MEDIAMTX_API_PASSWORD;
const mtxAuth = 'Basic ' + Buffer.from('esabong-api:' + password).toString('base64');
const login = await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'X-Tenant-Slug': slug, 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'admin', password: 'DemoPass#2026' }) })).json();
const H = { 'X-Tenant-Slug': slug, 'X-Requested-With': 'XMLHttpRequest', Authorization: `Bearer ${login.access_token}` };
const browser = await chromium.launch({ args: ['--disable-features=WebRtcHideLocalIpsWithMdns'] });

function dockerStats() {
  const line = execFileSync('docker', ['stats', '--no-stream', '--format', '{{.CPUPerc}}|{{.MemUsage}}', (process.env.BENCH_MEDIAMTX_CONTAINER ?? 'esabong-e2e-mediamtx')]).toString().trim();
  const [cpu, mem] = line.split('|');
  return { cpu: parseFloat(cpu), memMiB: parseFloat(mem) * (mem.includes('GiB') ? 1024 : 1) };
}
/** Bytes the MediaMTX container has transmitted (all media leaving the server). */
function txBytes() {
  return Number(execFileSync('docker', ['exec', (process.env.BENCH_MEDIAMTX_CONTAINER ?? 'esabong-e2e-mediamtx'), 'cat', '/sys/class/net/eth0/statistics/tx_bytes']).toString().trim());
}
function hostCpuPct() {
  const total = execFileSync('ps', ['-A', '-o', '%cpu=']).toString().split('\n').reduce((a, x) => a + (parseFloat(x) || 0), 0);
  return +(total / Number(execFileSync('sysctl', ['-n', 'hw.ncpu']).toString())).toFixed(1);
}
async function outboundBytes() {
  const r = await (await fetch((process.env.BENCH_MEDIAMTX_API ?? 'http://127.0.0.1:19997') + '/v3/paths/list', { headers: { Authorization: mtxAuth } })).json();
  return r.items.reduce((sum, p) => sum + (p.outboundBytes ?? p.bytesSent ?? 0), 0);
}
async function apiStats() {
  return execFileSync('ps', ['-o', '%cpu=,rss=', '-p', execFileSync('pgrep', ['-f', 'uvicorn app.main:app --host 127.0.0.1 --port 8016']).toString().trim().split('\n')[0]]).toString().trim();
}

const results = [];
for (const n of levels.map(Number)) {
  const pb = (await (await fetch(`${API}/admin/streams/${streamId}/preview`, { method: 'POST', headers: H })).json()).playback;
  const url = pb.url.replace('/stream/webrtc', MTX);
  const pages = [];
  const perPage = 100;
  const t0 = Date.now();
  for (let i = 0; i < n; i += perPage) {
    const ctx = await browser.newContext();
    const page = await ctx.newPage(); page.on('console', (m) => console.error('[page]', m.text())); page.on('pageerror', (e) => console.error('[pageerror]', e.message)); await page.goto(`${MTX}/`);
    pages.push(page);
  }
  console.error('pages ready', pages.length);
  // Join: all pages in parallel, each opening its share 10 at a time.
  const joins = await Promise.all(pages.map((page, idx) => page.evaluate(async ({ url, count }) => {
    window.pcs = []; const started = performance.now(); let failed = 0; const firstPacket = [];
    for (let i = 0; i < count; i += 10) {
      await Promise.all(Array.from({ length: Math.min(10, count - i) }, async () => {
        const t = performance.now();
        const pc = new RTCPeerConnection(); pc.addTransceiver('video', { direction: 'recvonly' }); pc.addTransceiver('audio', { direction: 'recvonly' });
        await pc.setLocalDescription(await pc.createOffer());
        try {
          const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/sdp' }, body: pc.localDescription.sdp });
          if (r.status !== 201) { failed++; pc.close(); return; }
          await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() });
          window.pcs.push(pc);
          await new Promise((ok) => { const check = setInterval(async () => { let got = 0; (await pc.getStats()).forEach((s) => { if (s.type === 'inbound-rtp' && s.kind === 'video') got = s.packetsReceived; }); if (got > 0 || performance.now() - t > 15000) { clearInterval(check); firstPacket.push(got > 0 ? performance.now() - t : null); ok(); } }, 50); });
        } catch { failed++; pc.close(); }
      }));
    }
    return { joined: window.pcs.length, failed, joinMs: performance.now() - started, firstPacket };
  }, { url, count: Math.min(perPage, n - idx * perPage) })));
  console.error('joined', JSON.stringify(joins.map((j) => [j.joined, j.failed])));
  const joinSeconds = +((Date.now() - t0) / 1000).toFixed(1);
  const joined = joins.reduce((a, j) => a + j.joined, 0), failed = joins.reduce((a, j) => a + j.failed, 0);
  const firstPackets = joins.flatMap((j) => j.firstPacket).filter((x) => x !== null).sort((a, b) => a - b);
  // Hold and observe.
  const holdSeconds = 30, samples = [];
  const tx0 = txBytes(), tBytes0 = Date.now();
  const snapshot = () => Promise.all(pages.map((p) => p.evaluate(async () => {
    const out = [];
    for (const pc of window.pcs) {
      let v = null; (await pc.getStats()).forEach((s) => { if (s.type === 'inbound-rtp' && s.kind === 'video') v = { recv: s.packetsReceived, lost: s.packetsLost, jitter: s.jitter, decoded: s.framesDecoded ?? 0, bytes: s.bytesReceived }; });
      out.push({ state: pc.connectionState, ...v });
    }
    return out;
  })));
  let before = (await snapshot()).flat();
  const recv0 = before.reduce((a, s) => a + (s.bytes ?? 0), 0);
  let stalled = 0, disconnected = 0;
  console.error('holding');
  for (let t = 0; t < holdSeconds; t += 5) {
    await new Promise((r) => setTimeout(r, 5000));
    const stats = dockerStats();
    const now = (await snapshot()).flat();
    stalled += now.filter((s, i) => before[i] && s.recv !== undefined && s.recv === before[i].recv).length;
    disconnected = now.filter((s) => s.state !== 'connected').length;
    samples.push({ ...stats, host: hostCpuPct(), api: await apiStats() });
    before = now;
  }
  const tx1 = txBytes(), seconds = (Date.now() - tBytes0) / 1000;
  const final = (await snapshot()).flat();
  const recv1 = final.reduce((a, s) => a + (s.bytes ?? 0), 0);
  const totalRecv = final.reduce((a, s) => a + (s.recv ?? 0), 0), totalLost = final.reduce((a, s) => a + Math.max(0, s.lost ?? 0), 0);
  const jit = final.map((s) => (s.jitter ?? 0) * 1000).sort((a, b) => a - b);
  const q = (arr, p) => arr.length ? arr[Math.min(arr.length - 1, Math.floor(p * arr.length))] : null;
  const row = {
    viewers: n, joined, failed, joinSeconds,
    firstPacketMs: { p50: Math.round(q(firstPackets, 0.5)), p95: Math.round(q(firstPackets, 0.95)) },
    holdSeconds, stalledViewerIntervals: stalled, disconnectedAtEnd: disconnected,
    packetLossPct: +(100 * totalLost / Math.max(1, totalRecv + totalLost)).toFixed(3),
    jitterMs: { p50: +q(jit, 0.5)?.toFixed(1), p95: +q(jit, 0.95)?.toFixed(1) },
    decodedFramesPerViewer: Math.round(final.reduce((a, s) => a + (s.decoded ?? 0), 0) / Math.max(1, final.length)),
    receivedMbpsTotal: +(((recv1 - recv0) * 8) / seconds / 1e6).toFixed(1),
    receivedMbpsPerViewer: +(((recv1 - recv0) * 8) / seconds / 1e6 / Math.max(1, joined)).toFixed(2),
    mediamtxTxMbps: +(((tx1 - tx0) * 8) / seconds / 1e6).toFixed(1),
    hostCpuPctAvg: +(samples.reduce((a, s) => a + s.host, 0) / samples.length).toFixed(1),
    mediamtxCpuPct: { avg: +(samples.reduce((a, s) => a + s.cpu, 0) / samples.length).toFixed(1), max: Math.max(...samples.map((s) => s.cpu)) },
    mediamtxMemMiB: Math.round(Math.max(...samples.map((s) => s.memMiB))),
    apiProcess: samples.at(-1)?.api,
  };
  console.log(JSON.stringify(row)); fs.appendFileSync(outFile, JSON.stringify(row) + '\n'); results.push(row);
  for (const p of pages) { await p.evaluate(() => window.pcs.forEach((pc) => pc.close())); await p.context().close(); }
  await new Promise((r) => setTimeout(r, 8000)); // let MediaMTX release sessions
}
await browser.close();

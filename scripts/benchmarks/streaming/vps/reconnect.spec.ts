import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { PLAYER_URL, tenantLogin } from '../../../../web/e2e/helpers';

/** Real player recovery: broadcaster outage, then a MediaMTX restart. Needs the bench stack. */
const docker = (...a: string[]) => execFileSync('docker', a).toString().trim();
const publisher = (key: string) => docker('run', '-d', '--name', 'esabong-bench-obs', '--network', 'esabong-bench-net', '--entrypoint', 'ffmpeg', 'bluenviron/mediamtx:1.21.3-ffmpeg',
  '-hide_banner', '-loglevel', 'error', '-re', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
  '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-g', '30', '-b:v', '2500k', '-c:a', 'aac', '-b:a', '128k', '-f', 'flv', `rtmp://esabong-bench-mediamtx:1935/live/${key}`);

test('player recovers from a broadcaster outage and a media-server restart', async ({ page }) => {
  test.setTimeout(240_000);
  const key = process.env['BENCH_OBS_KEY']!;
  let whep = 0;
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().includes('/whep')) whep++; });
  // Frames actually presented (a WebRTC <video>'s currentTime advances even without frames).
  const frames = () => page.locator('pp-video-stage video').first().evaluate((v: HTMLVideoElement & { __f?: number }) => {
    if (v.__f === undefined) { v.__f = 0; const c = () => { v.__f = (v.__f ?? 0) + 1; v.requestVideoFrameCallback(c); }; v.requestVideoFrameCallback(c); }
    return v.__f;
  });
  async function advancing(): Promise<void> { const s = await frames(); await expect.poll(frames, { timeout: 120_000, intervals: [100] }).toBeGreaterThan(s + 10); }
  async function stalled(): Promise<void> { let last = await frames(); await expect.poll(async () => { const n = await frames(); const same = n === last; last = n; return same; }, { timeout: 30_000, intervals: [1000] }).toBe(true); }
  try { docker('rm', '-f', 'esabong-bench-obs'); } catch { /* none */ }
  publisher(key);
  await tenantLogin(page, PLAYER_URL, 'player1');
  await expect(page.locator('pp-video-stage .live-badge')).toBeVisible({ timeout: 60_000 });
  await advancing();
  const results: Record<string, unknown> = { initialWhepSessions: whep };

  // 1. Broadcaster offline for 5 s (OBS crash / uplink drop), then back.
  docker('rm', '-f', 'esabong-bench-obs');
  await stalled();
  await page.waitForTimeout(5000);
  let t = Date.now(); const before1 = whep;
  publisher(key);
  await advancing();
  results['broadcasterBackToPictureSeconds'] = (Date.now() - t) / 1000;
  results['reconnectAttemptsAfterOutage'] = whep - before1;

  // 2. MediaMTX restart (deploy, crash). Viewers and the broadcaster both reconnect.
  t = Date.now(); const before2 = whep;
  docker('restart', 'esabong-bench-mediamtx');
  try { docker('rm', '-f', 'esabong-bench-obs'); } catch { /* exited */ }
  publisher(key);
  await stalled().catch(() => undefined);
  await advancing();
  results['serverRestartToPictureSeconds'] = (Date.now() - t) / 1000;
  results['reconnectAttemptsAfterRestart'] = whep - before2;
  console.log('RECONNECT ' + JSON.stringify(results));
  docker('rm', '-f', 'esabong-bench-obs');
});

import { Browser, BrowserContext, Page, expect, test } from '@playwright/test';
import { ADMIN_URL, DEMO_PASSWORD, PLAYER_URL, apiCall, apiLogin, openArena, tenantLogin, unique } from './helpers';

/**
 * Stream Operator end to end against a real MediaMTX: an administrator creates an Operator and
 * assigns a stream; the Operator signs in to their own console, broadcasts the (fake) camera
 * from the browser over WHIP, records, plays the recording back; players watch live, recover
 * from a server-side interruption, and see the stream go offline when the Operator stops.
 *
 * Needs E2E_STREAMING=1, MediaMTX reachable through the dev proxy, and Google Chrome (H.264 in
 * MP4 for recording playback). E2E_MTX_API_URL / E2E_MTX_API_PASSWORD let the test interrupt the
 * broadcast through MediaMTX's control API (skipped when unset).
 */
test.skip(!process.env['E2E_STREAMING'], 'Set E2E_STREAMING=1 with MediaMTX running (docs/live-streaming/README.md)');
test.use({
  channel: 'chrome',
  launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] },
});

async function presentedFrames(page: Page, scope: string): Promise<number> {
  return page.locator(`${scope} video`).first().evaluate((v: HTMLVideoElement & { __frames?: number }) => {
    if (v.__frames === undefined) {
      v.__frames = 0;
      const count = () => {
        v.__frames = (v.__frames ?? 0) + 1;
        v.requestVideoFrameCallback(count);
      };
      v.requestVideoFrameCallback(count);
    }
    return v.__frames;
  });
}

async function expectAdvancing(page: Page, scope: string, timeout = 20_000): Promise<void> {
  const start = await presentedFrames(page, scope);
  await expect.poll(() => presentedFrames(page, scope), { timeout }).toBeGreaterThan(start + 15);
}

/**
 * E2E_SYNTHETIC_CAMERA=1 replaces getUserMedia with an animated 1280x720 canvas and a tone, for
 * hosts where the OS blocks camera access for automated browsers (macOS privacy settings block
 * even Chrome's fake device there). Everything after capture (WHIP, MediaMTX, recording,
 * playback) is unchanged; the browser permission prompt itself is then not exercised.
 */
function syntheticCamera(): void {
  const devices = [
    { deviceId: 'synthetic-camera', kind: 'videoinput', label: 'Synthetic camera', groupId: 'g' },
    { deviceId: 'synthetic-mic', kind: 'audioinput', label: 'Synthetic microphone', groupId: 'g' },
  ];
  navigator.mediaDevices.enumerateDevices = async () => devices.map((d) => ({ ...d, toJSON: () => d })) as unknown as MediaDeviceInfo[];
  navigator.mediaDevices.getUserMedia = async () => {
    const canvas = Object.assign(document.createElement('canvas'), { width: 1280, height: 720 });
    const g = canvas.getContext('2d')!;
    let frame = 0;
    setInterval(() => {
      frame++;
      g.fillStyle = `hsl(${frame % 360} 70% 40%)`;
      g.fillRect(0, 0, 1280, 720);
      g.fillStyle = '#fff';
      g.font = 'bold 96px sans-serif';
      g.fillText(`frame ${frame}`, 80, 380);
    }, 1000 / 30);
    const video = canvas.captureStream(30).getVideoTracks()[0];
    const audio = new AudioContext();
    const tone = audio.createOscillator();
    const out = audio.createMediaStreamDestination();
    tone.connect(out);
    tone.start();
    void audio.resume();
    const vSettings = video.getSettings.bind(video);
    video.getSettings = () => ({ ...vSettings(), deviceId: 'synthetic-camera', width: 1280, height: 720 });
    const track = out.stream.getAudioTracks()[0];
    const aSettings = track.getSettings.bind(track);
    track.getSettings = () => ({ ...aSettings(), deviceId: 'synthetic-mic' });
    return new MediaStream([video, track]);
  };
}

async function session(browser: Browser, url: string, username: string, media = false): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext(media ? { permissions: ['camera', 'microphone'] } : {});
  if (media && process.env['E2E_SYNTHETIC_CAMERA']) await context.addInitScript(syntheticCamera);
  const page = await context.newPage();
  await tenantLogin(page, url, username);
  await expect(page).not.toHaveURL(/\/login/);
  return { context, page };
}

/** Kick the browser's WHIP publisher on MediaMTX (a real interruption of the ingest). */
async function interruptPublisher(pathId: string): Promise<boolean> {
  const base = process.env['E2E_MTX_API_URL'];
  const password = process.env['E2E_MTX_API_PASSWORD'];
  if (!base || !password) return false;
  const auth = { Authorization: `Basic ${Buffer.from(`esabong-api:${password}`).toString('base64')}` };
  const list = await (await fetch(`${base}/v3/webrtcsessions/list`, { headers: auth })).json();
  const pub = (list.items as { id: string; path: string; state: string }[]).find((s) => s.path === `live/${pathId}` && s.state === 'publish');
  if (!pub) return false;
  return (await fetch(`${base}/v3/webrtcsessions/kick/${pub.id}`, { method: 'POST', headers: auth })).ok;
}

test('operator broadcasts from the browser, records, and players watch live', async ({ browser, request }) => {
  test.setTimeout(300_000);
  const token = await apiLogin(request, 'admin');
  const call = (method: 'get' | 'post' | 'put' | 'patch', path: string, data?: unknown) => apiCall(request, token, method, path, data);
  const health = await (await call('get', '/admin/streams/health')).json();
  expect(health.configured && health.reachable, JSON.stringify(health)).toBeTruthy();

  // Setup: a running event, an enabled browser/OBS stream for it, and an Operator assigned to it.
  const eventName = unique('Operator derby');
  const event = await (await call('post', '/admin/events', { name: eventName, starts_at: '2030-06-01T18:00:00+08:00' })).json();
  const match = await (await call('post', `/admin/events/${event.id}/matches`, { meron_name: 'Red', wala_name: 'Blue' })).json();
  for (const endpoint of [`events/${event.id}/publish`, `events/${event.id}/start`, `matches/${match.id}/start`]) {
    expect((await call('post', `/admin/${endpoint}`)).ok()).toBeTruthy();
  }
  const stream = await (await call('post', '/admin/streams', { name: unique('Operator cam'), source_kind: 'obs', event_id: event.id, enabled: true })).json();
  const pathId = /live\/([\w-]{24})\//.exec(stream.ingest.whip_path)![1];
  const username = `camop-${Date.now().toString(36)}`;
  const op = await (await call('post', '/admin/users', { username, full_name: 'Camera Operator', account_type: 'stream_operator', password: DEMO_PASSWORD })).json();
  expect((await call('put', `/admin/operators/${op.user.id}/streams`, { stream_ids: [stream.id] })).ok()).toBeTruthy();

  try {
    // 1. The Operator lands on their own console and cannot open administration pages.
    const { page: console_ } = await session(browser, ADMIN_URL, username, true);
    await expect(console_).toHaveURL(/\/operator$/);
    await expect(console_.getByRole('heading', { name: 'Operator console' })).toBeVisible();
    await console_.goto(`${ADMIN_URL}/users`);
    await expect(console_).toHaveURL(/\/operator$/);
    await expect(console_.getByRole('navigation', { name: 'Main' })).toHaveCount(0);

    // 2. Camera + microphone preview, then a 720p browser broadcast goes live.
    await console_.getByRole('button', { name: /Enable camera/ }).click();
    await expect.poll(() => console_.locator('video[aria-label="Camera preview"]').evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2);
    await console_.getByRole('button', { name: 'Start streaming' }).click();
    const state = console_.locator('.screen .state');
    await expect(state).toHaveAttribute('data-state', 'live', { timeout: 30_000 });
    await expect(console_.locator('.stats')).toContainText('Mbps', { timeout: 15_000 });

    // 3. A player watches it in the existing arena, with the LIVE indicator.
    const { page: player } = await session(browser, PLAYER_URL, 'player1');
    await openArena(player, eventName);
    await expect(player.locator('pp-video-stage .live-badge')).toHaveCount(1, { timeout: 30_000 });
    await expect(player.locator('pp-video-stage')).toContainText(/Live · (WebRTC|HLS)/, { timeout: 30_000 });
    await expectAdvancing(player, 'pp-video-stage');
    await expect(player.getByRole('button', { name: 'Start streaming' })).toHaveCount(0);

    // 4. Record while live, stop, and play the recording back.
    await console_.getByRole('button', { name: 'Start recording' }).click();
    await expect(console_.locator('.screen .rec')).toBeVisible({ timeout: 15_000 });
    await console_.waitForTimeout(8_000);
    await console_.getByRole('button', { name: 'Stop recording' }).click();
    const row = console_.locator('.recordings tbody tr').first();
    await expect(row.locator('.pill')).toHaveText('completed', { timeout: 30_000 });
    await row.getByRole('button', { name: 'Play' }).click();
    const playback = console_.locator('mat-dialog-container video');
    await expect.poll(() => playback.evaluate((v: HTMLVideoElement) => (v.readyState >= 2 ? v.duration : 0)), { timeout: 20_000 }).toBeGreaterThan(5);
    const download = await console_.locator('mat-dialog-container a[download]').first().getAttribute('href');
    const file = await request.get(new URL(download!, ADMIN_URL).toString());
    expect(file.status()).toBe(200);
    expect(file.headers()['content-type']).toBe('video/mp4');
    await console_.getByRole('button', { name: 'Close' }).click();

    // 5. Interruption: MediaMTX drops the publisher; the console reconnects and players recover.
    if (await interruptPublisher(pathId)) {
      await expect(state).not.toHaveAttribute('data-state', 'live', { timeout: 15_000 });
      await expect(state).toHaveAttribute('data-state', 'live', { timeout: 45_000 });
      await expectAdvancing(player, 'pp-video-stage', 45_000);
    }

    // 6. Stop: players see the offline placeholder.
    await console_.getByRole('button', { name: 'Stop streaming' }).click();
    await console_.getByRole('button', { name: 'Stop broadcast' }).click();
    await expect(state).toHaveAttribute('data-state', /offline|reconnecting/, { timeout: 15_000 });
    await expect(player.locator('pp-video-stage .live-badge')).toHaveCount(0, { timeout: 60_000 });
    await expect(player.locator('pp-video-stage')).toContainText(/offline|interrupted|reconnect/i, { timeout: 60_000 });

    // 7. The administrator sees the Operator and the finished session.
    const { page: admin } = await session(browser, ADMIN_URL, 'admin');
    await admin.goto(`${ADMIN_URL}/operators`);
    await expect(admin.getByRole('heading', { name: 'Stream Operators' })).toBeVisible();
    await expect(admin.locator('table').first()).toContainText(username);
    await expect(admin.getByText('Recent sessions')).toBeVisible({ timeout: 45_000 });
  } finally {
    await call('patch', `/admin/streams/${stream.id}`, { enabled: false });
  }
});

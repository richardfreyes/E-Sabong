import { Browser, Page, expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { DEMO_PASSWORD, ADMIN_URL, PLAYER_URL, apiCall, apiLogin, navTo, openArena, tenantLogin, unique } from './helpers';

/**
 * Live streaming end to end against a real MediaMTX: an administrator creates and previews a
 * stream, players watch it over WebRTC (or HLS where WebRTC is unavailable), the video survives
 * fight, result and realtime updates without restarting, and recovers from an interruption.
 *
 * Needs the streaming service (E2E_STREAMING=1, MediaMTX reachable through the dev proxy);
 * the development test pattern is the only video source, so no external feed is required.
 */
test.skip(!process.env['E2E_STREAMING'], 'Set E2E_STREAMING=1 with MediaMTX running (docs/live-streaming/README.md)');

/**
 * Video frames the element has actually presented. A WebRTC <video>'s currentTime keeps
 * advancing even when no frames arrive, so count presented frames instead.
 */
async function presentedFrames(page: Page, scope = 'pp-video-stage'): Promise<number> {
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

async function expectAdvancing(page: Page, scope = 'pp-video-stage'): Promise<void> {
  const start = await presentedFrames(page, scope);
  await expect.poll(() => presentedFrames(page, scope), { timeout: 15_000 }).toBeGreaterThan(start + 15);
}

/** WHEP session requests: each one is a (re)connection of the WebRTC player. */
function countWhep(page: Page): () => number {
  let posts = 0;
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/stream\/webrtc\/live\/[\w-]{24}(\/rtc)?\/whep/.test(r.url())) posts++;
  });
  return () => posts;
}

async function player(browser: Browser, eventName: string, init?: () => void): Promise<Page> {
  const context = await browser.newContext();
  if (init) await context.addInitScript(init);
  const page = await context.newPage();
  await tenantLogin(page, PLAYER_URL, 'player1');
  await expect(page).not.toHaveURL(/\/login/);
  await openArena(page, eventName);
  return page;
}

test('admin previews, players watch live without restarts, declarators monitor, HLS fallback and recovery', async ({ browser, request }) => {
  test.setTimeout(240_000);
  const token = await apiLogin(request, 'admin');
  const call = (method: 'get' | 'post' | 'put' | 'patch', path: string, data?: unknown) => apiCall(request, token, method, path, data);
  const health = await (await call('get', '/admin/streams/health')).json();
  expect(health.configured && health.reachable, JSON.stringify(health)).toBeTruthy();

  const eventName = unique('Live stream derby');
  const event = await (await call('post', '/admin/events', { name: eventName, starts_at: '2030-06-01T18:00:00+08:00' })).json();
  const match = await (await call('post', `/admin/events/${event.id}/matches`, { meron_name: 'Red', wala_name: 'Blue' })).json();
  for (const endpoint of [`events/${event.id}/publish`, `events/${event.id}/start`, `matches/${match.id}/start`]) {
    expect((await call('post', `/admin/${endpoint}`)).ok()).toBeTruthy();
  }
  const desk = await (
    await call('post', '/admin/users', { username: `stream-desk-${Date.now().toString(36)}`, full_name: 'Stream desk', account_type: 'declarator', password: DEMO_PASSWORD })
  ).json();
  expect((await call('put', `/declarator/events/${event.id}/assignments/${desk.user.id}`)).ok()).toBeTruthy();
  const streamName = unique('Arena test pattern');
  let streamId = '';
  try {
    // 1. The administrator adds a test-pattern stream for this arena, hidden from players.
    const adminContext = await browser.newContext();
    const admin = await adminContext.newPage();
    await tenantLogin(admin, ADMIN_URL, 'admin');
    await navTo(admin, 'Live streaming');
    await admin.getByLabel('Stream name').fill(streamName);
    await admin.getByLabel('Source').click();
    await admin.getByRole('option', { name: /Test pattern/ }).click();
    await admin.getByLabel('Arena (event)').first().click();
    await admin.getByRole('option', { name: eventName }).click();
    await admin.getByRole('button', { name: 'Add stream' }).click();
    const card = admin.getByRole('article', { name: `Stream ${streamName}` });
    await expect(card).toBeVisible();
    streamId = (await (await call('get', '/admin/streams')).json()).find((s: { name: string }) => s.name === streamName).id;

    // 2. Players cannot see it yet; the administrator tests playback first.
    const viewer = await player(browser, eventName);
    await expect(viewer.locator('pp-video-stage .live-badge')).toHaveCount(0);
    await card.getByRole('button', { name: 'Test playback' }).click();
    await expect(card.locator('pp-video-stage .live-badge')).toBeVisible({ timeout: 20_000 });
    await expectAdvancing(admin, `article[aria-label="Stream ${streamName}"] pp-video-stage`);

    // 3. Enabled for players: the arena picks it up through realtime, over WebRTC.
    const whepPosts = countWhep(viewer);
    await card.getByRole('switch', { name: 'Visible to players' }).click();
    await expect(viewer.locator('pp-video-stage .live-badge')).toBeVisible({ timeout: 20_000 });
    await expect(viewer.locator('pp-video-stage .source-badge')).toContainText('Live · WebRTC');
    await expectAdvancing(viewer);
    const connections = whepPosts();
    expect(connections).toBe(1);

    // 4. Fight, result and realtime updates must not restart the video.
    const judge = await apiLogin(request, desk.user.username);
    const declared = await apiCall(request, judge, 'post', `/declarator/matches/${match.id}/declarations`, { request_id: randomUUID(), outcome: 'meron' });
    expect(declared.ok(), await declared.text()).toBeTruthy();
    expect((await declared.json()).status).toBe('finalized');
    for (let n = 0; n < 2; n++) {
      expect((await call('post', `/admin/events/${event.id}/matches`, { meron_name: `Red ${n}`, wala_name: `Blue ${n}` })).ok()).toBeTruthy();
    }
    await expect(viewer.getByText(/Official result|MERON wins|Meron wins/i).first()).toBeVisible({ timeout: 15_000 });
    await expectAdvancing(viewer);
    expect(whepPosts()).toBe(connections);

    // 5. The assigned declarator monitors the same stream in the declarator desk.
    const deskContext = await browser.newContext();
    const deskPage = await deskContext.newPage();
    await tenantLogin(deskPage, ADMIN_URL, desk.user.username);
    await expect(deskPage).toHaveURL(/declarator/);
    await expect(deskPage.locator('pp-video-stage .live-badge')).toBeVisible({ timeout: 20_000 });
    await expect(deskPage.locator('pp-video-stage').getByRole('button', { name: 'Reconnect', exact: true })).toBeVisible();
    await expectAdvancing(deskPage);

    // 6. Without WebRTC the player uses Low-Latency HLS.
    const hlsViewer = await player(browser, eventName, () => {
      delete (window as unknown as { RTCPeerConnection?: unknown }).RTCPeerConnection;
    });
    await expect(hlsViewer.locator('pp-video-stage .source-badge')).toContainText('Live · HLS', { timeout: 30_000 });
    await expectAdvancing(hlsViewer);

    // 7. An interruption (the source restarts) recovers without the viewer doing anything.
    expect((await call('post', `/admin/streams/${streamId}/reconnect`)).ok()).toBeTruthy();
    await expect.poll(whepPosts, { timeout: 45_000 }).toBeGreaterThan(connections);
    await expect(viewer.locator('pp-video-stage .live-badge')).toBeVisible({ timeout: 45_000 });
    await expectAdvancing(viewer);

    // 8. Hidden again: players lose the stream at once, the administrator preview remains possible.
    await card.getByRole('switch', { name: 'Visible to players' }).click();
    await expect(viewer.locator('pp-video-stage .live-badge')).toHaveCount(0, { timeout: 20_000 });
    for (const context of [adminContext, deskContext, viewer.context(), hlsViewer.context()]) await context.close();
  } finally {
    if (streamId) await request.delete(`${process.env['E2E_API_URL'] || 'http://localhost:8000'}/api/v1/admin/streams/${streamId}`, {
      headers: { 'X-Tenant-Slug': process.env['E2E_TENANT_SLUG'] || 'demo', Authorization: `Bearer ${token}` },
    });
  }
});

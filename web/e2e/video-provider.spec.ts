import { expect, test, Page } from '@playwright/test';
import {
  ADMIN_URL,
  PLAYER_URL,
  DEMO_PASSWORD,
  apiCall,
  apiLogin,
  tenantLogin,
  openArena,
  unique,
} from './helpers';

test('licensed demo plays in existing Player and Declarator containers', async ({
  browser,
  request,
}) => {
  const token = await apiLogin(request, 'admin');
  const call = (method: 'get' | 'post' | 'put', path: string, data?: unknown) =>
    apiCall(request, token, method, path, data);
  const path = '/admin/settings/video-provider';
  const original = await (await call('get', path)).json();
  delete original.credentials_saved;
  delete original.status;
  delete original.checked_at;
  const username = `video-desk-${Date.now().toString(36)}`;
  const account = await (
    await call('post', '/admin/users', {
      username,
      full_name: 'Video test desk',
      account_type: 'declarator',
      password: DEMO_PASSWORD,
    })
  ).json();
  const name = unique('Authorized demo video');
  const event = await (
    await call('post', '/admin/events', { name, starts_at: '2030-06-01T18:00:00+08:00' })
  ).json();
  const match = await (
    await call('post', `/admin/events/${event.id}/matches`, {
      meron_name: 'Red',
      wala_name: 'Blue',
    })
  ).json();
  for (const endpoint of [
    `events/${event.id}/publish`,
    `events/${event.id}/start`,
    `matches/${match.id}/start`,
  ]) {
    expect((await call('post', `/admin/${endpoint}`)).ok()).toBeTruthy();
  }
  expect(
    (await call('put', `/declarator/events/${event.id}/assignments/${account.user.id}`)).ok(),
  ).toBeTruthy();
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const admin = await contexts[0].newPage();
    await tenantLogin(admin, ADMIN_URL, 'admin');
    await expect(admin).not.toHaveURL(/login/);
    await admin.goto(`${ADMIN_URL}/settings`);
    await admin.getByRole('tab', { name: 'External Video Provider' }).click();
    await admin.getByLabel('Provider adapter').click();
    await admin.getByRole('option', { name: 'Licensed demo' }).click();
    await admin.getByLabel('Enable provider', { exact: true }).check();
    await admin.getByLabel('Assigned Internal Event ID', { exact: true }).fill(event.id);
    await admin.getByRole('button', { name: 'Save Configuration', exact: true }).click();
    await expect(admin.getByRole('status')).toContainText('Configuration saved');
    const verify = async (page: Page) => {
      await expect
        .poll(
          () =>
            page.locator('pp-video-stage video').evaluate((v: HTMLVideoElement) => v.videoWidth),
          { timeout: 30000 },
        )
        .toBeGreaterThan(0);
      await expect(page.locator('pp-video-stage')).toContainText('Demo video');
      const play = page.getByRole('button', {name:'Play',exact:true});
      if (await play.isVisible()) await play.click();
      await expect.poll(() => page.locator('pp-video-stage video').evaluate((v: HTMLVideoElement) => v.currentTime), {timeout:15000}).toBeGreaterThan(0);
      await expect(page.locator('pp-video-stage video')).toHaveJSProperty('paused',false);
    };
    const player = await contexts[1].newPage();
    await tenantLogin(player, PLAYER_URL, 'player1');
    await expect(player).not.toHaveURL(/login/);
    await openArena(player, name);
    await verify(player);
    const desk = await contexts[2].newPage();
    await tenantLogin(desk, ADMIN_URL, username);
    await expect(desk).toHaveURL(/declarator/);
    await verify(desk);
    await desk.getByRole('button', { name: 'Refresh Video', exact: true }).click();
    await verify(desk);
    await desk.getByRole('button', { name: 'Refetch Event', exact: true }).click();
    await expect(desk.locator('pp-video-stage')).toContainText(/Demonstration|busy/);
    await expect(desk.getByRole('button', { name: /Declare\s*Meron/ })).toBeVisible();
    expect((await (await call('get', `/portal/matches/${match.id}`)).json()).result).toBeNull();
    await admin.getByRole('button', { name: 'Disable Provider', exact: true }).click();
    await expect(admin.getByRole('status')).toContainText('Configuration saved');
    await expect(player.locator('pp-video-stage')).toContainText(/disabled|busy/);
  } finally {
    await call('put', path, original);
    await call('post', `/admin/users/${account.user.id}/suspend`);
    for (const context of contexts) await context.close();
  }
});

import { expect, test } from '@playwright/test';

import { ADMIN_URL, PLAYER_URL, TENANT_SLUG, apiLogin, apiCall, navTo, newSession, tenantLogin, unique } from './helpers';

test.describe('Tenant admin and player portals', () => {
  test('players cannot use the administration portal', async ({ page }) => {
    await tenantLogin(page, ADMIN_URL, 'player1');
    await expect(page.getByRole('alert')).toContainText('no administration access');
  });

  test('event lifecycle with a live result pushed to the player', async ({ page, browser, request }) => {
    const eventName = unique('E2E Derby');
    await tenantLogin(page, ADMIN_URL, 'admin');
    await expect(page.getByRole('heading', { name: /Hello/ })).toBeVisible();

    await navTo(page, 'Events');
    await page.getByRole('button', { name: 'New event' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Name', { exact: true }).fill(eventName);
    await dialog.getByLabel('Venue').fill('Test Arena');
    await dialog.getByLabel('Starts').fill('2030-06-01T18:00');
    await dialog.getByRole('button', { name: 'Create draft' }).click();
    await expect(page.getByRole('heading', { name: eventName })).toBeVisible();

    // Routine fight entry is Declarator-driven: the Events page has no Add match control.
    await expect(page.getByRole('button', { name: /Add match/ })).toHaveCount(0);
    const eventId = new URL(page.url()).pathname.split('/events/')[1].split('/')[0];
    const admin = await apiLogin(request, 'admin');
    await apiCall(request, admin, 'post', `/admin/events/${eventId}/matches`, { meron_name: 'Red Comet', wala_name: 'Blue Ridge' });
    await page.reload();
    await expect(page.getByText('Red Comet')).toBeVisible();

    await page.getByRole('button', { name: 'Publish' }).click();
    await page.getByRole('button', { name: 'Go live' }).click();
    await page.getByRole('button', { name: 'Start', exact: true }).click();
    await expect(page.getByText('In progress', { exact: false }).first()).toBeVisible();

    // The player watches the fight page while the operator declares the result.
    const player = await newSession(browser, PLAYER_URL, 'player1');
    await navTo(player, 'Events');
    // Search rather than rely on the new event's position among everything else that is live.
    await player.getByPlaceholder('Search events or venues').fill(eventName);
    await player.getByRole('link', { name: new RegExp(eventName) }).click();
    await player.getByRole('link', { name: 'Fight 1, live', exact: true }).click();
    const official = player.getByRole('region', { name: 'Official result' });
    await expect(official).toContainText('Not declared yet');

    await page.getByRole('button', { name: 'Declare result' }).click();
    await page.getByRole('dialog').getByRole('radio', { name: 'Meron wins' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Declare result' }).click();
    await expect(page.getByText('Official result: Meron wins')).toBeVisible();

    // Arrives over the WebSocket, without a reload.
    await expect(official).toContainText('Meron wins', { timeout: 15_000 });
    await player.context().close();
  });

  test('Balance history and header use the same gameplay balance', async ({ page, request }) => {
    const admin = await apiLogin(request, 'admin');
    const settings = await (await apiCall(request, admin, 'get', '/admin/betting/settings')).json();
    await apiCall(request, admin, 'put', '/admin/betting/settings', { enabled: true, starting_balance: '5000.00' });
    try {
    await tenantLogin(page, PLAYER_URL, 'player3');
    await expect(page).not.toHaveURL(/\/login/);
    await page.goto(`${PLAYER_URL}/arena-points`);
    await expect(page.getByRole('heading', { name: 'Balance history' })).toBeVisible();
    const header = page.locator('[data-field="header-balance"]');
    await expect(header).toHaveCount(1);
    await expect(page.locator('p.balance')).toHaveText(await header.locator('.pp-num').innerText());
    await expect(page.locator('header .balance')).toHaveCount(1);
    await expect(page.locator('main')).not.toContainText(/\b(?:pts|cr|credits)\b/i);
    } finally {
      await apiCall(request, admin, 'put', '/admin/betting/settings', { enabled: settings.enabled, starting_balance: settings.starting_balance });
    }
  });

  test('branding changes apply without a rebuild', async ({ page, browser }) => {
    const name = unique('Arena');
    await tenantLogin(page, ADMIN_URL, 'admin');
    await navTo(page, 'Settings');
    await page.getByRole('tab', { name: 'Branding', exact: true }).click();
    const appName = page.getByLabel('Application name');
    await expect(appName).not.toHaveValue('');
    const original = await appName.inputValue();
    try {
      await appName.fill(name);
      await page.getByRole('button', { name: 'Save branding' }).click();
      await expect(page.getByText('Branding saved')).toBeVisible();

      const context = await browser.newContext();
      const visitor = await context.newPage();
      await visitor.goto(`${PLAYER_URL}/login?tenant=${encodeURIComponent(TENANT_SLUG)}`);
      await expect(visitor).toHaveTitle(new RegExp(name));
      await expect(visitor.getByText(name).first()).toBeVisible();
      await context.close();
    } finally {
      await appName.fill(original);
      await page.getByRole('button', { name: 'Save branding' }).click();
      await expect(page.getByText('Branding saved').last()).toBeVisible();
    }
  });
});

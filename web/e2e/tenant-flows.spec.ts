import { expect, test } from '@playwright/test';

import { ADMIN_URL, PLAYER_URL, navTo, newSession, tenantLogin, unique } from './helpers';

test.describe('Tenant admin and player portals', () => {
  test('players cannot use the administration portal', async ({ page }) => {
    await tenantLogin(page, ADMIN_URL, 'player1');
    await expect(page.getByRole('alert')).toContainText('no administration access');
  });

  test('event lifecycle with a live result pushed to the player', async ({ page, browser }) => {
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

    await page.getByLabel('Meron', { exact: true }).fill('Red Comet');
    await page.getByLabel('Wala', { exact: true }).fill('Blue Ridge');
    await page.getByRole('button', { name: /Add match #1/ }).click();
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

  test('allocating demonstration points updates the player balance', async ({ page, browser }) => {
    const player = await newSession(browser, PLAYER_URL, 'player3');
    await navTo(player, 'Points history');
    const balance = player.locator('p.balance');
    await expect(balance).toHaveText(/\d/); // loaded, not the empty placeholder
    const before = Number((await balance.innerText()).replace(/[^0-9]/g, ''));

    await tenantLogin(page, ADMIN_URL, 'admin');
    await navTo(page, 'Demo points');
    await page.getByRole('tab', { name: 'Allocate' }).click();
    await page.getByRole('combobox', { name: 'Account' }).fill('player3');
    await page.getByRole('option', { name: /player3/ }).click();
    await page.getByLabel('Amount').fill('25');
    await page.getByLabel('Memo (optional)').fill('E2E allocation');
    await page.getByRole('button', { name: 'Allocate', exact: true }).last().click();
    await expect(page.getByText(/25 points → player3/)).toBeVisible();

    await expect(balance).toHaveText((before + 25).toLocaleString('en-US'), { timeout: 15_000 });
    await expect(player.getByText('E2E allocation').first()).toBeVisible();
    await player.context().close();
  });

  test('branding changes apply without a rebuild', async ({ page, browser }) => {
    const name = unique('Arena');
    await tenantLogin(page, ADMIN_URL, 'admin');
    await navTo(page, 'Settings');
    const appName = page.getByLabel('Application name');
    await expect(appName).not.toHaveValue('');
    const original = await appName.inputValue();
    try {
      await appName.fill(name);
      await page.getByRole('button', { name: 'Save branding' }).click();
      await expect(page.getByText('Branding saved')).toBeVisible();

      const context = await browser.newContext();
      const visitor = await context.newPage();
      await visitor.goto(`${PLAYER_URL}/login?tenant=demo`);
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

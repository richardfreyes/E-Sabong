import { expect, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { ADMIN_URL, OWNER_URL, PLAYER_URL, TENANT_SLUG, apiCall, apiLogin } from './helpers';
import { OWNER } from './global-setup';

// Explicit evidence job; the ordinary regression suite remains unchanged.
test('readiness visual inventory uses real scratch records at all nine widths', async ({
  browser,
  request,
}) => {
  test.skip(process.env['READINESS_VISUAL'] !== '1', 'Run the separate readiness evidence job');
  test.setTimeout(360_000);
  const output = resolve(process.env['UI_EVIDENCE_DIR'] || 'test-results/readiness-visual');
  await mkdir(output, { recursive: true });
  const portals = [
    {
      name: 'owner',
      base: OWNER_URL,
      user: OWNER.email,
      password: OWNER.password,
      routes: [
        '',
        'tenants',
        'signups',
        'plans',
        'subscriptions',
        'invoices',
        'licenses',
        'deployments',
        'feature-flags',
        'audit',
        'account',
      ],
    },
    {
      name: 'admin',
      base: ADMIN_URL,
      user: 'admin',
      password: 'DemoPass#2026',
      routes: [
        '',
        'users',
        'hierarchy',
        'roles',
        'events',
        'betting',
        'points',
        'reports',
        'audit',
        'settings',
        'subscription',
        'profile',
      ],
    },
    {
      name: 'player',
      base: PLAYER_URL,
      user: 'player1',
      password: 'DemoPass#2026',
      routes: [
        '',
        'events',
        'history',
        'predictions',
        'results',
        'arena-points',
        'notifications',
        'activity',
        'profile',
        'settings',
      ],
    },
    {
      name: 'declarator',
      base: ADMIN_URL,
      user: 'declarator',
      password: 'DemoPass#2026',
      routes: ['declarator'],
    },
  ];
  const inventory: unknown[] = [],
    failures: string[] = [];
  const admin = await apiLogin(request, 'admin');
  const previous = await (await apiCall(request, admin, 'get', '/admin/betting/settings')).json();
  const enabled = await apiCall(request, admin, 'put', '/admin/betting/settings', {
    enabled: true,
  });
  expect(enabled.status()).toBe(200);
  try {
    for (const portal of portals) {
      const context = await browser.newContext({ colorScheme: 'dark' });
      const page = await context.newPage();
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      try {
        await page.goto(`${portal.base}/login?tenant=${encodeURIComponent(TENANT_SLUG)}`);
        await page
          .getByLabel(portal.name === 'owner' ? 'Email' : 'Username', { exact: true })
          .fill(portal.user);
        await page.getByLabel('Password', { exact: true }).fill(portal.password);
        await page.getByRole('button', { name: 'Sign in' }).click();
        await expect(page).not.toHaveURL(/\/login/);
        for (const route of portal.routes) {
          await page.goto(`${portal.base}/${route}`);
          await page.locator('.mat-drawer-content main').waitFor();
          await page.evaluate(() => document.fonts.ready);
          for (const width of [320, 375, 390, 430, 768, 1024, 1280, 1440, 1920]) {
            await page.setViewportSize({ width, height: 1000 });
            await page.waitForTimeout(300);
            const measurement = await page.evaluate(() => {
              const content = document.querySelector('.mat-drawer-content')!;
              return {
                width: innerWidth,
                document: document.documentElement.scrollWidth,
                content: content.scrollWidth,
                client: content.clientWidth,
                headerBalances: document.querySelectorAll('[data-field="header-balance"]').length,
                headings: [...document.querySelectorAll('h1')].map((el) => el.textContent),
              };
            });
            const key = `${portal.name}-${route || 'dashboard'}-${width}`;
            await page.screenshot({ path: resolve(output, `${key}.png`), fullPage: true });
            if (measurement.document > width || measurement.content > measurement.client + 1)
              failures.push(`${key}: horizontal overflow`);
            if (portal.name === 'player' && measurement.headerBalances !== 1)
              failures.push(`${key}: expected one header balance`);
            inventory.push({ key, route: page.url(), ...measurement, errors: [...errors] });
          }
          await writeFile(resolve(output, 'inventory.json'), JSON.stringify(inventory, null, 2));
        }
        failures.push(...errors.map((error) => `${portal.name}: ${error}`));
      } finally {
        await context.close();
      }
    }
    await writeFile(resolve(output, 'failures.json'), JSON.stringify(failures, null, 2));
    expect(failures).toEqual([]);
  } finally {
    const restored = await apiCall(request, admin, 'put', '/admin/betting/settings', {
      enabled: previous.enabled,
    });
    expect(restored.status()).toBe(200);
  }
});

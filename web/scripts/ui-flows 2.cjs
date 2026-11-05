const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs/promises');
const path = require('node:path');
const phase = process.argv[2] || 'after';
const extras = process.argv[4] === 'extras';
const output = path.resolve(__dirname, '../../docs/ui-ux', phase);
const inventory = [];
async function capture(page, key) {
  const checks = [];
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.waitForTimeout(250);
    await page.evaluate(() => document.fonts.ready);
    checks.push(await page.evaluate(() => {
      const content = document.querySelector('.mat-drawer-content');
      const dialog = document.querySelector('[role="dialog"]');
      const r = dialog?.getBoundingClientRect();
      const clipped = [...document.querySelectorAll('main input, main button, main .pool b, main [data-field], [role="dialog"] input')].filter(el => {
        const r = el.getBoundingClientRect();
        if (!r.width || r.bottom < 0 || r.top > innerHeight) return false;
        if (el.closest('[aria-hidden="true"], .mat-tab-body:not(.mat-tab-body-active)')) return false;
        return el.scrollWidth > el.clientWidth + 2;
      }).slice(0, 12).map(el => ({ element: el.tagName + '.' + el.className, text: el.textContent?.trim().slice(0, 60), width: el.clientWidth, scroll: el.scrollWidth }));
      return { width: innerWidth, pageOverflow: document.documentElement.scrollWidth > innerWidth, contentOverflow: content ? content.scrollWidth > content.clientWidth + 1 : false, dialogOverflow: !!r && (r.left < 0 || r.right > innerWidth || r.top < 0 || r.bottom > innerHeight), clipped };
    }));
    await page.screenshot({ path: path.join(output, `${key}-${width}.png`) });
  }
  inventory.push({ key, checks });
  await fs.writeFile(path.join(output, extras ? 'flows-extras.json' : 'flows.json'), JSON.stringify(inventory, null, 2));
  console.log(key, JSON.stringify(checks.filter(c => c.pageOverflow || c.contentOverflow || c.dialogOverflow || c.clipped.length)));
}
async function tabs(page, key) {
  await page.getByRole('tab').first().waitFor();
  const names = await page.getByRole('tab').allTextContents();
  for (let i = 0; i < names.length; i++) {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('tab').nth(i).click();
    await page.waitForLoadState('networkidle');
    await capture(page, `${key}-${names[i].trim().toLowerCase().replace(/\W+/g, '-')}`);
  }
}
async function dialog(page, route, button, key, validate) {
  await page.goto(route);
  await page.getByRole('button', { name: button, exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await capture(page, key);
  if (validate) {
    await page.getByRole('dialog').getByRole('button', { name: validate, exact: true }).click();
    await expect(page.getByRole('dialog').locator('mat-error').first()).toBeVisible();
    await capture(page, key + '-validation');
  }
  await page.keyboard.press('Escape');
  // The one-time-license dialog intentionally requires its dismiss action.
  if (await page.getByRole('dialog').isVisible()) await page.getByRole('dialog').getByRole('button', { name: /Cancel|Close/ }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
}
(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch();
  try {
    for (const portal of (process.argv[3] ? process.argv[3].split(',') : ['owner', 'admin', 'player'])) {
      const base = portal === 'owner' ? 'http://localhost:4200' : portal === 'admin' ? 'http://localhost:4201/admin' : 'http://localhost:4202';
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
      const page = await context.newPage();
      page.on('pageerror', e => console.error(portal, e.message));
      if (extras && portal === 'player') {
        await page.goto(base + '/register?tenant=demo');
        await capture(page, 'player-registration');
      }
      await page.goto(base + '/login?tenant=demo');
      if (!extras) await capture(page, `${portal}-login`);
      await page.getByLabel(portal === 'owner' ? 'Email' : 'Username', { exact: true }).fill(portal === 'owner' ? 'owner@example.com' : portal === 'admin' ? 'admin' : 'player1');
      await page.getByLabel('Password', { exact: true }).fill(portal === 'owner' ? 'OwnerPass#2026' : 'DemoPass#2026');
      await page.getByRole('button', { name: 'Sign in' }).click();
      await page.waitForURL(url => !url.pathname.endsWith('/login'));
      if (extras) {
        if (portal !== 'owner') {
          await page.goto(base + '/change-password');
          await capture(page, `${portal}-change-password`);
        }
        if (portal === 'admin') {
          await page.goto(base + '/forbidden');
          await capture(page, 'admin-forbidden');
          await page.goto(base + '/hierarchy');
          await page.getByRole('button', { name: 'Expand master1', exact: true }).click();
          await page.getByRole('button', { name: 'Expand agent1', exact: true }).click();
          await capture(page, 'admin-hierarchy-expanded');
        }
        await page.goto(base + '/');
        await page.setViewportSize({ width: 1440, height: 1000 });
        await page.getByRole('button', { name: portal === 'player' ? 'Collapse menu' : 'Collapse navigation', exact: true }).click();
        await capture(page, `${portal}-navigation-collapsed`);
        await context.close();
        continue;
      }
      if (portal === 'owner') {
        await page.goto(base + '/tenants');
        await page.getByRole('row', { name: /SWC MAIN\.localhost/ }).click();
        await tabs(page, 'owner-tenant');
        await dialog(page, base + '/tenants', 'New tenant', 'owner-new-tenant', 'Create tenant');
        await dialog(page, base + '/plans', 'New plan', 'owner-new-plan', 'Save plan');
        await dialog(page, base + '/licenses', 'Issue license', 'owner-issue-license');
        await dialog(page, base + '/deployments', 'Register deployment', 'owner-deployment');
      }
      if (portal === 'admin') {
        await dialog(page, base + '/users', 'New account', 'admin-new-account', 'Create account');
        await dialog(page, base + '/events', 'New event', 'admin-new-event', 'Create draft');
        await page.goto(base + '/users');
        await page.getByPlaceholder('Search username, name, email').fill('admin');
        await page.getByRole('row', { name: /Demo Administrator/ }).click();
        await capture(page, 'admin-account-detail');
        await page.goto(base + '/events');
        await page.getByPlaceholder('Search name or venue').fill('Grand Derby Demo');
        await page.getByRole('row', { name: /Grand Derby Demo/ }).click();
        await capture(page, 'admin-event-detail');
        await page.goto(base + '/settings');
        await tabs(page, 'admin-settings');
        await page.goto(base + '/betting');
        await tabs(page, 'admin-betting');
        await page.goto(base + '/reports');
        await page.getByRole('radio', { name: 'Points', exact: true }).click();
        await capture(page, 'admin-points-report');
      }
      if (portal === 'player') {
        await page.goto(base + '/events');
        await page.locator('a.card').first().click();
        await capture(page, 'player-event-detail');
        await page.locator('pp-fight-list a').first().click();
        await capture(page, 'player-match-detail');
        await page.goto(base + '/');
        await page.getByRole('button', { name: 'Account menu', exact: true }).click();
        await capture(page, 'player-account-menu');
        await page.keyboard.press('Escape');
      }
      await page.goto(base + '/');
      await page.setViewportSize({ width: 320, height: 1000 });
      await page.getByRole('button', { name: portal === 'player' ? 'Open menu' : 'Open navigation', exact: true }).click();
      await capture(page, `${portal}-navigation-open`);
      await context.close();
    }
  } finally { await browser.close(); }
})();

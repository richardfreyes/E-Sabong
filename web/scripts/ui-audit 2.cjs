/* Read-only visual inventory against the running local demo portals. */
const { chromium } = require('@playwright/test');
const fs = require('node:fs/promises');
const path = require('node:path');
const phase = process.argv[2] || 'before';
const inventoryName = process.argv[3] ? `inventory-${process.argv.slice(3).join('-').replace(/[^\w-]/g, '-')}.json` : 'inventory.json';
const output = path.resolve(__dirname, '../../docs/ui-ux', phase);
const widths = [320, 375, 390, 430, 768, 1024, 1280, 1440, 1920];
const portals = [
  { name: 'owner', url: 'http://localhost:4200', user: 'owner@example.com', password: 'OwnerPass#2026', routes: ['', 'tenants', 'signups', 'plans', 'subscriptions', 'invoices', 'licenses', 'deployments', 'feature-flags', 'audit', 'account'] },
  { name: 'admin', url: 'http://localhost:4201/admin', user: 'admin', password: 'DemoPass#2026', routes: ['', 'users', 'hierarchy', 'roles', 'events', 'betting', 'points', 'reports', 'audit', 'settings', 'subscription', 'profile', 'declarator'] },
  { name: 'player', url: 'http://localhost:4202', user: 'player1', password: 'DemoPass#2026', routes: ['', 'events', 'history', 'predictions', 'results', 'points', 'notifications', 'activity', 'profile', 'settings'] },
];
(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch();
  const results = [];
  try {
    for (const portal of portals.filter(portal => !process.argv[3] || process.argv[3].split(',').includes(portal.name))) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'dark' });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`${portal.url}/login?tenant=demo`);
      await page.getByLabel(portal.name === 'owner' ? 'Email' : 'Username', { exact: true }).fill(portal.user);
      await page.getByLabel('Password', { exact: true }).fill(portal.password);
      await page.getByRole('button', { name: 'Sign in' }).click();
      await page.waitForURL(url => !url.pathname.endsWith('/login'));
      for (const route of portal.routes.filter(route => !process.argv[4] || route === process.argv[4])) {
        await page.goto(`${portal.url}/${route}`);
        // A login wall also contains <main>; it is not evidence for an authenticated route.
        await page.locator('.mat-drawer-content main').waitFor();
        await page.waitForLoadState('networkidle').catch(() => {});
        await page.evaluate(() => document.fonts.ready);
        const key = `${portal.name}-${route || 'dashboard'}`;
        const measurements = [];
        for (const width of widths) {
          await page.setViewportSize({ width, height: 1000 });
          // Allow responsive drawer transitions and ResizeObserver to finish.
          await page.waitForTimeout(300);
          measurements.push(await page.evaluate(() => {
            const main = document.querySelector('main');
            const content = document.querySelector('.mat-drawer-content') || main;
            const overflow = [...document.querySelectorAll('main *')].filter(el => {
              const r = el.getBoundingClientRect();
              if (r.width < 1 || r.right <= innerWidth + 1) return false;
              let parent = el.parentElement;
              while (parent && parent !== main) {
                if (['auto', 'scroll', 'hidden', 'clip'].includes(getComputedStyle(parent).overflowX)) return false;
                parent = parent.parentElement;
              }
              return true;
            }).slice(0, 12).map(el => `${el.tagName.toLowerCase()}.${String(el.className).replace(/\s+/g, '.').slice(0, 110)}`);
            return { width: innerWidth, document: document.documentElement.scrollWidth, content: content?.scrollWidth, client: content?.clientWidth, overflow };
          }));
          if ([320, 768, 1440].includes(width)) {
            await page.screenshot({ path: path.join(output, `${key}-${width}.png`), fullPage: true });
          }
        }
        const result = { key, url: page.url(), headings: await page.locator('h1').allTextContents(), measurements, errors: [...errors] };
        results.push(result);
        console.log(key, JSON.stringify(measurements.filter(m => m.overflow.length || m.document > m.width || m.content > m.client + 1)));
        await fs.writeFile(path.join(output, inventoryName), JSON.stringify(results, null, 2));
      }
      await context.close();
    }
  } finally { await browser.close(); }
})();

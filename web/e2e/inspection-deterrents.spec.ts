import { expect, test } from '@playwright/test';

const remote = process.env['INSPECTION_REMOTE_URL']?.replace(/\/$/, '');
const enabled = process.env['INSPECTION_CONFIGURATION'] !== 'development';
// This suite uses only public login pages. It never signs in or changes application data.
for (const [index, portal] of ['owner', 'admin', 'player'].entries()) {
  test(`${portal}: inspection policy and accessible form interactions`, async ({ browser }) => {
    const token = process.env['E2E_CONTABO_ACCESS_TOKEN'];
    if (remote && !token) throw new Error('Remote UAT checks require E2E_CONTABO_ACCESS_TOKEN');
    const context = await browser.newContext(remote ? { httpCredentials: { username: 'uat', password: token! } } : {});
    try {
      const page = await context.newPage();
      const base = remote ? `${remote}${portal === 'player' ? '' : `/${portal}`}` : `http://localhost:${4310 + index}${portal === 'admin' ? '/admin' : ''}`;
      if (remote) await page.goto(`${remote}/__uat/access`);
      else {
        // Public-page boot only: no live backend is required for local build-policy checks.
        await page.route('**/api/**', route => route.fulfill({ status: 401, json: { error: { code: 'unauthorized', message: 'Sign in required' } } }));
      }
      await page.goto(`${base}/login`);
      await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
      const user = page.getByLabel(portal === 'owner' ? 'Email' : 'Username', { exact: true });
      const password = page.getByLabel('Password', { exact: true });
      await user.fill(portal === 'owner' ? 'inspection@example.test' : 'inspection-check');
      await user.press('Tab');
      await expect(password).toBeFocused();
      await password.pressSequentially('Typing-123');
      await expect(password).toHaveValue('Typing-123');
      await password.press('ControlOrMeta+A');
      await password.pressSequentially('Replacement-456');
      await expect(password).toHaveValue('Replacement-456');

      const cancelled = await page.evaluate(() => {
        const keys: KeyboardEventInit[] = [
          { key: 'F12' },
          ...['I', 'J', 'C'].map(key => ({ key, code: `Key${key}`, ctrlKey: true, shiftKey: true })),
          ...['I', 'J', 'C'].map(key => ({ key, code: `Key${key}`, metaKey: true, altKey: true })),
        ];
        return keys.map(init => !document.body.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })));
      });
      expect(cancelled).toEqual(Array(7).fill(enabled));
      // Real Playwright key events exercise the focused form and browser-delivered listener.
      await page.evaluate(() => {
        (window as any).__inspectionKeys = [];
        window.addEventListener('keydown', event => (window as any).__inspectionKeys.push(event.defaultPrevented));
      });
      for (const shortcut of ['F12', 'Control+Shift+I', 'Control+Shift+J', 'Control+Shift+C', 'Meta+Alt+I', 'Meta+Alt+J', 'Meta+Alt+C']) {
        await password.press(shortcut);
        expect(await page.evaluate(() => (window as any).__inspectionKeys.at(-1)), shortcut).toBe(enabled);
      }
      const menus = await page.evaluate(() => {
        const fire = (target: Element, button: number) => !target.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, button }));
        return { page: fire(document.body, 2), input: fire(document.querySelector('input')!, 2), keyboard: fire(document.body, 0) };
      });
      expect(menus).toEqual({ page: enabled, input: false, keyboard: false });
      await page.evaluate(() => {
        (window as any).__inspectionMenu = undefined;
        window.addEventListener('contextmenu', event => (window as any).__inspectionMenu = event.defaultPrevented, { once: true });
      });
      await page.locator('body').click({ button: 'right', position: { x: 3, y: 3 } });
      expect(await page.evaluate(() => (window as any).__inspectionMenu)).toBe(enabled);
      await page.keyboard.press('Escape');
      expect(await page.evaluate(() => typeof (window as any).ng?.getComponent === 'function')).toBe(!enabled);
      // Console APIs stay usable; development-only Angular hooks are checked separately.
      expect(await page.evaluate(() => typeof console.debug)).toBe('function');
      const keyCost = await page.evaluate(() => {
        const started = performance.now();
        for (let i = 0; i < 5000; i++) document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true }));
        return (performance.now() - started) / 5000;
      });
      console.log(`${portal}: ordinary key dispatch ${keyCost.toFixed(4)} ms/event; deterrents=${enabled}`);
    } finally {
      await context.close();
    }
  });
}

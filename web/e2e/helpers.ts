import { Browser, Page, expect } from '@playwright/test';

export const OWNER_URL = 'http://localhost:4200';
export const ADMIN_URL = 'http://localhost:4201/admin';
export const PLAYER_URL = 'http://localhost:4202';
export const DEMO_PASSWORD = 'DemoPass#2026';

export function unique(prefix: string): string {
  return `${prefix} ${Date.now().toString(36)}`;
}

export async function tenantLogin(page: Page, baseUrl: string, username: string, password = DEMO_PASSWORD): Promise<void> {
  await page.goto(`${baseUrl}/login?tenant=demo`);
  await page.getByLabel('Username or email').fill(username);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

/** A separate browser context = a separate user session. */
export async function newSession(browser: Browser, baseUrl: string, username: string): Promise<Page> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await tenantLogin(page, baseUrl, username);
  await expect(page).not.toHaveURL(/\/login/);
  return page;
}

/** Clicks an item in the side navigation (avoids matching page CTAs with similar text). */
export async function navTo(page: Page, label: string): Promise<void> {
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: label, exact: true }).click();
}

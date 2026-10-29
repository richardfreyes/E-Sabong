import { APIRequestContext, Browser, Page, expect } from '@playwright/test';

export const OWNER_URL = 'http://localhost:4200';
export const ADMIN_URL = 'http://localhost:4201/admin';
export const PLAYER_URL = 'http://localhost:4202';
export const DEMO_PASSWORD = 'DemoPass#2026';

export function unique(prefix: string): string {
  return `${prefix} ${Date.now().toString(36)}`;
}

export async function tenantLogin(page: Page, baseUrl: string, username: string, password = DEMO_PASSWORD): Promise<void> {
  await page.goto(`${baseUrl}/login?tenant=demo`);
  await page.getByLabel('Username', { exact: true }).fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
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

/** Direct API access for test setup (accounts, events, fight lifecycle) on the demo tenant. */
export const API = 'http://localhost:8000/api/v1';
export const TENANT = { 'X-Tenant-Slug': 'demo' };

export async function apiLogin(api: APIRequestContext, username: string, password = DEMO_PASSWORD, headers: Record<string, string> = TENANT): Promise<string> {
  const r = await api.post(`${API}/auth/login`, { headers, data: { username, password } });
  expect(r.status(), await r.text()).toBe(200);
  return (await r.json()).access_token;
}

export function apiCall(api: APIRequestContext, token: string, method: 'get' | 'post' | 'put' | 'patch', path: string, data?: unknown, headers: Record<string, string> = TENANT) {
  return api[method](`${API}${path}`, { headers: { ...headers, Authorization: `Bearer ${token}` }, data });
}

/** Points the player's arena at one event (the arena otherwise follows the featured event). */
export async function openArena(page: Page, eventName: string): Promise<void> {
  await page.getByRole('button', { name: 'Switch event' }).click();
  await page.getByRole('menuitem', { name: new RegExp(eventName) }).click();
  await expect(page.locator('.where .name')).toHaveText(eventName);
}


import { Injectable, isDevMode, signal } from '@angular/core';

const STORAGE_KEY = 'esb.devTenant';

/**
 * In production the tenant is resolved by the API from the request host. During
 * local development every app runs on localhost, so `?tenant=<slug>` selects a
 * tenant; it is sent as X-Tenant-Slug, which the API only honours when
 * ALLOW_TENANT_HEADER=true.
 */
@Injectable({ providedIn: 'root' })
export class TenantContext {
  readonly devSlug = signal<string | null>(null);

  constructor() {
    if (!isDevMode()) return;
    const fromQuery = new URLSearchParams(location.search).get('tenant');
    let stored: string | null = null;
    try {
      if (fromQuery) sessionStorage.setItem(STORAGE_KEY, fromQuery);
      stored = sessionStorage.getItem(STORAGE_KEY);
    } catch {
      stored = fromQuery;
    }
    this.devSlug.set(fromQuery || stored || 'demo');
  }
}

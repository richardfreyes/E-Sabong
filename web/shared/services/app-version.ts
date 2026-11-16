import { DestroyRef, Injectable, inject, signal } from '@angular/core';

/**
 * The API compatibility level of this frontend build, sent on every API request as `X-ESB-Client`.
 * Raise it together with the server's `DESK_MIN_CLIENT_VERSION` only when an older Declarator desk
 * must stop calling the API (its refresh behaviour or contract is obsolete). Version 2 is the first
 * desk that refreshes only the selected fight's book.
 */
export const APP_CLIENT_VERSION = 2;
export const CLIENT_VERSION_HEADER = 'X-ESB-Client';

/** The hashed main bundle a page was built with, e.g. `main-ABC123.js`. */
export function bundleOf(html: string): string | null {
  return html.match(/main-[A-Za-z0-9_-]+\.js/)?.[0] ?? null;
}

/**
 * Detects a newer deployment of this app while a page stays open.
 *
 * `available`: the server now serves a different build. Reload when convenient.
 * `required`: the API refused this build (426 `client_outdated`). The page must stop working and reload
 * once pending work is resolved.
 */
@Injectable({ providedIn: 'root' })
export class AppVersion {
  readonly available = signal(false);
  readonly required = signal(false);
  private readonly loaded = bundleOf(
    Array.from(document.querySelectorAll('script[src]'))
      .map((s) => s.getAttribute('src') ?? '')
      .join(' '),
  );
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.stop());
  }

  /** Poll for a new build every `intervalMs` while the page is visible. */
  watch(intervalMs = 60_000): void {
    if (this.timer || !this.loaded) return;
    this.timer = setInterval(() => {
      if (!document.hidden) void this.check();
    }, intervalMs);
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Compare the served index.html's bundle with the one this page runs. */
  async check(): Promise<boolean> {
    if (!this.loaded || this.available()) return this.available();
    try {
      const response = await fetch(new URL('index.html', document.baseURI).toString(), {
        cache: 'no-store',
        credentials: 'same-origin',
      });
      if (!response.ok) return false;
      const served = bundleOf(await response.text());
      if (served && served !== this.loaded) this.available.set(true);
    } catch {
      // Offline or between deploys: try again on the next tick.
    }
    return this.available();
  }

  /** The API refused this build. */
  markRequired(): void {
    this.available.set(true);
    this.required.set(true);
  }

  reload(): void {
    location.reload();
  }
}

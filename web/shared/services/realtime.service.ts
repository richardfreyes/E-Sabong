import { DestroyRef, Injectable, inject, isDevMode, signal } from '@angular/core';
import { Observable, Subject, filter, map } from 'rxjs';

import type { RealtimeMessage } from '@shared/models';
import { AuthStore } from './auth.store';
import { TenantContext } from './tenant-context';

type ConnectionState = 'idle' | 'connecting' | 'open' | 'closed';

/**
 * One WebSocket per tab to /api/ws. Reconnects with backoff and always uses the
 * current access token, so a token-expiry close (4408) recovers transparently.
 */
@Injectable({ providedIn: 'root' })
export class RealtimeService {
  private readonly auth = inject(AuthStore);
  private readonly tenant = inject(TenantContext);
  private readonly messages = new Subject<RealtimeMessage>();
  private socket: WebSocket | null = null;
  private retries = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private wanted = false;

  readonly state = signal<ConnectionState>('idle');
  readonly messages$ = this.messages.asObservable();

  constructor() {
    inject(DestroyRef).onDestroy(() => this.disconnect());
  }

  on<T = Record<string, unknown>>(...types: string[]): Observable<T> {
    return this.messages$.pipe(
      filter((m) => types.includes(m.type)),
      map((m) => m.data as T),
    );
  }

  connect(): void {
    this.wanted = true;
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;
    const token = this.auth.token();
    if (!token) return;
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const params = new URLSearchParams({ token });
    // The dev servers share localhost; pass the tenant the same way HTTP calls do.
    const slug = isDevMode() ? this.tenant.devSlug() : null;
    if (slug) params.set('tenant', slug);
    this.state.set('connecting');
    const ws = new WebSocket(`${scheme}://${location.host}/api/ws?${params}`);
    this.socket = ws;
    ws.onopen = () => {
      this.retries = 0;
      this.state.set('open');
      this.pingTimer = setInterval(() => ws.readyState === WebSocket.OPEN && ws.send('{"type":"ping"}'), 25_000);
    };
    ws.onmessage = (ev) => {
      try {
        this.messages.next(JSON.parse(ev.data as string) as RealtimeMessage);
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = (ev) => {
      this.state.set('closed');
      if (this.pingTimer) clearInterval(this.pingTimer);
      this.socket = null;
      if (!this.wanted) return;
      if (ev.code === 4401 || ev.code === 4408) {
        this.auth.refresh().subscribe((t) => t && this.schedule(500));
        return;
      }
      this.schedule(Math.min(30_000, 1000 * 2 ** this.retries++));
    };
  }

  disconnect(): void {
    this.wanted = false;
    if (this.timer) clearTimeout(this.timer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.socket?.close(1000);
    this.socket = null;
    this.state.set('idle');
  }

  private schedule(delay: number): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.connect(), delay);
  }
}

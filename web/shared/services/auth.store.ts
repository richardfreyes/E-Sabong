import { HttpBackend, HttpClient, HttpHeaders } from '@angular/common/http';
import { DestroyRef, Injectable, InjectionToken, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, catchError, finalize, firstValueFrom, map, of, shareReplay, tap, throwError } from 'rxjs';

import type { LoginResponse, TokenResponse } from '@shared/models';
import { API_BASE, toApiError } from './api-client';
import { TenantContext } from './tenant-context';

export interface AuthConfig {
  /** Path prefix of the auth endpoints, e.g. '/auth' or '/platform/auth'. */
  basePath: string;
  loginRoute: string;
  /** Route shown when the account still has a temporary password. */
  changePasswordRoute?: string;
  /**
   * Tenant portals that share one origin (admin at /admin/, player at /) name themselves so the
   * API keeps a separate refresh cookie for each. Without it, signing in to one portal would
   * replace the other portal's session in the shared cookie jar.
   */
  portal?: 'admin' | 'player';
}

export const AUTH_CONFIG = new InjectionToken<AuthConfig>('AUTH_CONFIG');

type Status = 'unknown' | 'authenticated' | 'anonymous';

interface ProfileLike {
  id?: string;
  permissions?: string[];
  must_change_password?: boolean;
}

/** Tells the other tabs of the SAME portal that its one session changed. */
type SessionSignal = { type: 'signed-in'; user: string | null } | { type: 'signed-out' };

/**
 * Holds the access token in memory only; the refresh token lives in an HttpOnly
 * cookie. Requests made here bypass interceptors (HttpBackend) to avoid loops.
 */
@Injectable({ providedIn: 'root' })
export class AuthStore<U extends object = ProfileLike> {
  private readonly config = inject(AUTH_CONFIG);
  private readonly router = inject(Router);
  private readonly tenant = inject(TenantContext);
  private readonly http = new HttpClient(inject(HttpBackend));

  private readonly _token = signal<string | null>(null);
  private readonly _user = signal<U | null>(null);
  private readonly _status = signal<Status>('unknown');
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  private refreshing$: Observable<string | null> | null = null;
  // Tabs of one portal share that portal's refresh cookie; other portals use their own channel.
  private readonly channel =
    typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(`esb.auth.${this.config.portal ?? this.config.basePath}`);

  constructor() {
    if (!this.channel) return;
    this.channel.onmessage = (event: MessageEvent<SessionSignal>) => void this.onSessionSignal(event.data);
    inject(DestroyRef).onDestroy(() => this.channel?.close());
  }

  readonly token = this._token.asReadonly();
  readonly user = this._user.asReadonly();
  readonly status = this._status.asReadonly();
  readonly isAuthenticated = computed(() => this._status() === 'authenticated');
  readonly permissions = computed(() => new Set((this._user() as ProfileLike | null)?.permissions ?? []));

  hasPermission(...perms: string[]): boolean {
    const granted = this.permissions();
    return perms.every((p) => granted.has(p));
  }

  hasAnyPermission(...perms: string[]): boolean {
    const granted = this.permissions();
    return perms.some((p) => granted.has(p));
  }

  private headers(): HttpHeaders {
    let h = new HttpHeaders({ 'X-Requested-With': 'XMLHttpRequest' });
    if (this.config.portal) h = h.set('X-Portal', this.config.portal);
    const slug = this.tenant.devSlug();
    if (slug) h = h.set('X-Tenant-Slug', slug);
    return h;
  }

  private url(path: string): string {
    return `${API_BASE}${this.config.basePath}${path}`;
  }

  async login(body: Record<string, string>): Promise<U> {
    try {
      const res = await firstValueFrom(
        this.http.post<LoginResponse<U>>(this.url('/login'), body, { headers: this.headers(), withCredentials: true }),
      );
      this.accept(res, res.user);
      this.announce({ type: 'signed-in', user: this.userId(res.user) });
      return res.user;
    } catch (err) {
      throw toApiError(err);
    }
  }

  /** Adopts a session obtained elsewhere (unit tests). */
  acceptSession(res: LoginResponse<U>): void {
    this.accept(res, res.user);
  }

  /** Self-registration also signs the user in, so it must set this portal's session cookie. */
  async register(body: Record<string, unknown>): Promise<U> {
    try {
      const res = await firstValueFrom(
        this.http.post<LoginResponse<U>>(this.url('/register'), body, { headers: this.headers(), withCredentials: true }),
      );
      this.accept(res, res.user);
      this.announce({ type: 'signed-in', user: this.userId(res.user) });
      return res.user;
    } catch (err) {
      throw toApiError(err);
    }
  }

  /** Restores a session from the refresh cookie at startup. */
  async restore(): Promise<void> {
    const token = await firstValueFrom(this.refresh());
    if (!token) return;
    try {
      const me = await firstValueFrom(this.http.get<U>(this.url('/me'), { headers: this.authHeaders(token) }));
      this._user.set(me);
      this._status.set('authenticated');
    } catch {
      this.clear();
    }
  }

  async reloadProfile(): Promise<U | null> {
    const token = this._token();
    if (!token) return null;
    const me = await firstValueFrom(this.http.get<U>(this.url('/me'), { headers: this.authHeaders(token) }));
    this._user.set(me);
    return me;
  }

  /** Single-flight refresh; resolves to the new token or null when the session is gone. */
  refresh(): Observable<string | null> {
    if (this.refreshing$) return this.refreshing$;
    this.refreshing$ = this.http
      .post<TokenResponse>(this.url('/refresh'), {}, { headers: this.headers(), withCredentials: true })
      .pipe(
        tap((res) => this.setToken(res)),
        map((res) => res.access_token),
        catchError(() => {
          this.clear();
          return of(null);
        }),
        finalize(() => (this.refreshing$ = null)),
        shareReplay(1),
      );
    return this.refreshing$;
  }

  async logout(redirect = true): Promise<void> {
    try {
      await firstValueFrom(
        this.http.post(this.url('/logout'), {}, { headers: this.headers(), withCredentials: true }).pipe(catchError(() => of(null))),
      );
    } finally {
      this.clear();
      this.announce({ type: 'signed-out' });
      if (redirect) await this.router.navigateByUrl(this.config.loginRoute);
    }
  }

  /** Called by the auth interceptor when the session cannot be recovered. */
  sessionExpired(): void {
    this.clear();
    void this.router.navigate([this.config.loginRoute], { queryParams: { reason: 'expired' } });
  }

  passwordChanged(): void {
    const user = this._user();
    if (user) this._user.set({ ...user, must_change_password: false } as U);
  }

  get changePasswordRoute(): string | undefined {
    return this.config.changePasswordRoute;
  }

  get loginRoute(): string {
    return this.config.loginRoute;
  }

  private userId(user: U | null): string | null {
    return (user as ProfileLike | null)?.id ?? null;
  }

  private announce(signal: SessionSignal): void {
    try {
      this.channel?.postMessage(signal);
    } catch {
      /* channel closed: nothing to tell */
    }
  }

  /** Another tab of this portal signed out, or signed in as someone else. */
  private async onSessionSignal(signal: SessionSignal): Promise<void> {
    if (signal.type === 'signed-out') {
      if (this._status() !== 'authenticated') return;
      this.clear();
      await this.router.navigate([this.config.loginRoute], { queryParams: { reason: 'signed-out' } });
      return;
    }
    if (this._status() === 'authenticated' && signal.user && signal.user === this.userId(this._user())) return;
    // The shared cookie now belongs to the newly signed-in account: adopt it instead of
    // continuing as the previous user until the next refresh.
    await this.restore();
    await this.router.navigateByUrl(this._status() === 'authenticated' ? '/' : this.config.loginRoute);
  }

  private authHeaders(token: string): HttpHeaders {
    return this.headers().set('Authorization', `Bearer ${token}`);
  }

  private accept(res: TokenResponse, user: U): void {
    this.setToken(res);
    this._user.set(user);
    this._status.set('authenticated');
  }

  private setToken(res: TokenResponse): void {
    this._token.set(res.access_token);
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    // Refresh a minute before expiry so requests rarely see a 401.
    const delay = Math.max(10, res.expires_in - 60) * 1000;
    this.refreshTimer = setTimeout(() => this.refresh().subscribe(), delay);
  }

  private clear(): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.refreshTimer = null;
    this._token.set(null);
    this._user.set(null);
    this._status.set('anonymous');
  }
}

export function rethrow(err: unknown): Observable<never> {
  return throwError(() => toApiError(err));
}

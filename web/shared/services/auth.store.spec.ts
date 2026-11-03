import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AUTH_CONFIG, AuthStore } from './auth.store';

// The store navigates to its login route after a sign-out or expiry; register it so those
// navigations resolve instead of rejecting after the test has finished.
const ROUTES = [{ path: 'login', children: [] }];

describe('AuthStore', () => {
  let store: AuthStore<{ permissions: string[]; must_change_password: boolean }>;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter(ROUTES),
        { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } },
      ],
    });
    store = TestBed.inject(AuthStore) as typeof store;
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('keeps the token in memory and exposes permissions after login', async () => {
    const pending = store.login({ username: 'admin', password: 'secret' });
    const req = http.expectOne('/api/v1/auth/login');
    expect(req.request.withCredentials).toBe(true);
    expect(req.request.headers.get('X-Requested-With')).toBe('XMLHttpRequest');
    req.flush({ access_token: 'tok', token_type: 'bearer', expires_in: 900, user: { permissions: ['users.manage'], must_change_password: false } });
    await pending;

    expect(store.isAuthenticated()).toBe(true);
    expect(store.token()).toBe('tok');
    expect(store.hasPermission('users.manage')).toBe(true);
    expect(store.hasAnyPermission('audit.view', 'users.manage')).toBe(true);
    expect(store.hasPermission('audit.view')).toBe(false);
  });

  it('shares one refresh request between concurrent callers', () => {
    const results: (string | null)[] = [];
    store.refresh().subscribe((t) => results.push(t));
    store.refresh().subscribe((t) => results.push(t));
    http.expectOne('/api/v1/auth/refresh').flush({ access_token: 'new', token_type: 'bearer', expires_in: 900 });
    expect(results).toEqual(['new', 'new']);
  });

  it('becomes anonymous when the session cannot be refreshed', async () => {
    const restored = store.restore();
    http.expectOne('/api/v1/auth/refresh').flush({ error: { code: 'invalid_refresh_token' } }, { status: 401, statusText: 'Unauthorized' });
    await restored;
    expect(store.status()).toBe('anonymous');
    expect(store.token()).toBeNull();
  });
});

/** Admin (/admin/) and Player (/) share one origin; each portal must use only its own session. */
describe('AuthStore portal sessions', () => {
  function setup(portal: 'admin' | 'player') {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter(ROUTES),
        { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login', portal } },
      ],
    });
    return { store: TestBed.inject(AuthStore), http: TestBed.inject(HttpTestingController) };
  }

  it('names its portal on login, restore, refresh and logout so the API uses that portal cookie', async () => {
    const { store, http } = setup('admin');
    const login = store.login({ username: 'admin', password: 'secret' });
    const loginReq = http.expectOne('/api/v1/auth/login');
    expect(loginReq.request.headers.get('X-Portal')).toBe('admin');
    loginReq.flush({ access_token: 'a', token_type: 'bearer', expires_in: 900, user: { id: 'u1', permissions: ['users.manage'] } });
    await login;

    const restore = store.restore();
    const refreshReq = http.expectOne('/api/v1/auth/refresh');
    expect(refreshReq.request.headers.get('X-Portal')).toBe('admin');
    expect(refreshReq.request.withCredentials).toBe(true);
    refreshReq.flush({ access_token: 'b', token_type: 'bearer', expires_in: 900 });
    await vi.waitFor(() => {
      const me = http.expectOne('/api/v1/auth/me');
      expect(me.request.headers.get('X-Portal')).toBe('admin');
      me.flush({ id: 'u1', permissions: ['users.manage'] });
    });
    await restore;
    expect(store.isAuthenticated()).toBe(true);

    const logout = store.logout(false);
    const logoutReq = http.expectOne('/api/v1/auth/logout');
    expect(logoutReq.request.headers.get('X-Portal')).toBe('admin');
    logoutReq.flush({ message: 'Signed out' });
    await logout;
    expect(store.isAuthenticated()).toBe(false);
  });

  it('follows sign-out and account changes from other tabs of the same portal only', async () => {
    const channels: { name: string; onmessage: ((e: MessageEvent) => void) | null; posted: unknown[] }[] = [];
    vi.stubGlobal(
      'BroadcastChannel',
      class {
        onmessage: ((e: MessageEvent) => void) | null = null;
        posted: unknown[] = [];
        constructor(public name: string) {
          channels.push(this);
        }
        postMessage(m: unknown) {
          this.posted.push(m);
        }
        close() {}
      },
    );
    try {
      const { store, http } = setup('player');
      expect(channels.map((c) => c.name)).toEqual(['esb.auth.player']);
      store.acceptSession({ access_token: 't', token_type: 'bearer', expires_in: 900, user: { id: 'p1', permissions: [] } } as never);

      // Same account signed in again elsewhere: nothing to do.
      channels[0].onmessage?.({ data: { type: 'signed-in', user: 'p1' } } as MessageEvent);
      http.expectNone('/api/v1/auth/refresh');

      // Another tab of this portal signed out: this tab ends its copy of the session too.
      channels[0].onmessage?.({ data: { type: 'signed-out' } } as MessageEvent);
      await vi.waitFor(() => expect(store.isAuthenticated()).toBe(false));

      // Another tab signed in as someone else: adopt the portal's new session.
      channels[0].onmessage?.({ data: { type: 'signed-in', user: 'p2' } } as MessageEvent);
      const refresh = http.expectOne('/api/v1/auth/refresh');
      expect(refresh.request.headers.get('X-Portal')).toBe('player');
      refresh.flush({ access_token: 'n', token_type: 'bearer', expires_in: 900 });
      await vi.waitFor(() => http.expectOne('/api/v1/auth/me').flush({ id: 'p2', permissions: [] }));
      await vi.waitFor(() => expect((store.user() as { id: string }).id).toBe('p2'));
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

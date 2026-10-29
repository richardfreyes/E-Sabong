import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AUTH_CONFIG, AuthStore } from './auth.store';

describe('AuthStore', () => {
  let store: AuthStore<{ permissions: string[]; must_change_password: boolean }>;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
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

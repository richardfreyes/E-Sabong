import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';

import { clientVersionInterceptor } from '../interceptors';
import { APP_CLIENT_VERSION, AppVersion, CLIENT_VERSION_HEADER, bundleOf } from './app-version';

describe('AppVersion', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.querySelectorAll('script[data-test-bundle]').forEach((s) => s.remove());
    TestBed.resetTestingModule();
  });

  const loadedWith = (bundle: string) => {
    const script = document.createElement('script');
    script.setAttribute('src', bundle);
    script.setAttribute('data-test-bundle', '');
    document.head.appendChild(script);
  };

  it('reads the hashed main bundle from markup', () => {
    expect(bundleOf('<script src="main-AB12_c-9.js" type="module"></script>')).toBe('main-AB12_c-9.js');
    expect(bundleOf('<html></html>')).toBeNull();
  });

  it('flags a new deployment when the served index.html names another bundle', async () => {
    loadedWith('main-OLD111.js');
    const version = TestBed.inject(AppVersion);
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response('<script src="main-OLD111.js"></script>'));
    expect(await version.check()).toBe(false);
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response('<script src="main-NEW222.js"></script>'));
    expect(await version.check()).toBe(true);
    expect(version.available()).toBe(true);
    expect(version.required()).toBe(false);
  });

  it('a refusal from the API makes the reload required', () => {
    const version = TestBed.inject(AppVersion);
    version.markRequired();
    expect(version.required()).toBe(true);
    expect(version.available()).toBe(true);
  });
});

describe('clientVersionInterceptor', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('sends the client version on API calls only', async () => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptors([clientVersionInterceptor])), provideHttpClientTesting()],
    });
    const http = TestBed.inject(HttpClient);
    const backend = TestBed.inject(HttpTestingController);
    void firstValueFrom(http.get('/api/v1/declarator/events'));
    void firstValueFrom(http.get('/admin/index.html'));
    expect(backend.expectOne('/api/v1/declarator/events').request.headers.get(CLIENT_VERSION_HEADER)).toBe(
      String(APP_CLIENT_VERSION),
    );
    expect(backend.expectOne('/admin/index.html').request.headers.has(CLIENT_VERSION_HEADER)).toBe(false);
  });

  it('once refused, Declarator calls fail locally and never reach the network', async () => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptors([clientVersionInterceptor])), provideHttpClientTesting()],
    });
    TestBed.inject(AppVersion).markRequired();
    const http = TestBed.inject(HttpClient);
    const backend = TestBed.inject(HttpTestingController);
    await expect(firstValueFrom(http.get('/api/v1/declarator/events/e/monitor'))).rejects.toMatchObject({
      status: 426,
      code: 'client_outdated',
    });
    backend.expectNone('/api/v1/declarator/events/e/monitor');
    // Other API calls (sign-out, profile) still work.
    void firstValueFrom(http.get('/api/v1/auth/me'));
    backend.expectOne('/api/v1/auth/me');
  });
});

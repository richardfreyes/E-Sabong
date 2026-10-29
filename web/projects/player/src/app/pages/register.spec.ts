import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { PublicTenantConfig } from '@shared/models';
import { AUTH_CONFIG, AuthStore, BrandingService } from '@shared/services';
import { RegisterPage } from './register';

describe('RegisterPage', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } },
      ],
    });
    TestBed.inject(BrandingService).config.set({ allow_self_registration: true, branding: { app_name: 'Arena' } } as unknown as PublicTenantConfig);
  });

  it('requires a strong password and the points acknowledgement', () => {
    const page = TestBed.createComponent(RegisterPage).componentInstance;
    page.form.setValue({ username: 'newplayer', full_name: 'New Player', email: '', password: 'alllowercase', ack: false });
    page.submit();
    expect(page.form.controls.password.errors?.['server']).toContain('three of');
    expect(page.form.controls.ack.valid).toBe(false);
    TestBed.inject(HttpTestingController).expectNone('/api/v1/auth/register');
  });

  it('registers and signs the player in', () => {
    const page = TestBed.createComponent(RegisterPage).componentInstance;
    page.form.setValue({ username: 'newplayer', full_name: 'New Player', email: '', password: 'Str0ng!Passw0rd', ack: true });
    page.submit();
    const req = TestBed.inject(HttpTestingController).expectOne('/api/v1/auth/register');
    expect(req.request.body).toEqual({ username: 'newplayer', full_name: 'New Player', email: null, password: 'Str0ng!Passw0rd' });
    req.flush({ access_token: 'tok', token_type: 'bearer', expires_in: 900, user: { permissions: [], must_change_password: false } });
    expect(TestBed.inject(AuthStore).isAuthenticated()).toBe(true);
  });
});

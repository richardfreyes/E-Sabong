import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { PublicTenantConfig } from '@shared/models';
import { AUTH_CONFIG, AuthStore, BrandingService, RealtimeService } from '@shared/services';
import { AdminLayout } from './layout';

function setup(permissions: string[], modules: string[]) {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      provideRouter([]),
      { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } },
      { provide: RealtimeService, useValue: { connect: () => undefined } },
    ],
  });
  TestBed.inject(AuthStore).acceptSession({
    access_token: 't', token_type: 'bearer', expires_in: 900,
    user: { permissions, must_change_password: false, account_type: 'agent', full_name: 'Agent', tenant: { name: 'T' } },
  } as never);
  TestBed.inject(BrandingService).config.set({ modules, branding: { app_name: 'T' } } as unknown as PublicTenantConfig);
  return TestBed.createComponent(AdminLayout).componentInstance;
}

describe('AdminLayout navigation', () => {
  it('shows only what an agent may use', () => {
    const layout = setup(['dashboard.view', 'users.view_downline', 'users.manage_downline', 'points.transfer', 'points.view_downline', 'reports.view'],
      ['events', 'matches', 'points', 'reports']);
    expect(layout.nav().map((n) => n.label)).toEqual(['Dashboard', 'Accounts', 'Hierarchy', 'Demo points', 'Reports']);
  });

  it('hides modules the tenant has disabled even with permission', () => {
    const layout = setup(['events.manage', 'points.view_all', 'reports.view'], ['events', 'matches']);
    expect(layout.nav().map((n) => n.label)).toEqual(['Events']);
  });

  it('prints each section heading once', () => {
    const layout = setup(['users.manage', 'roles.manage', 'audit.view', 'settings.manage', 'billing.view'], []);
    expect(layout.nav().filter((n) => n.section).map((n) => n.section)).toEqual(['People', 'Workspace']);
  });
});

it('gives Declarators the dedicated workspace without operational administration links', () => {
  const layout = setup(['results.submit'], ['events', 'matches']);
  expect(layout.nav().map(n => n.label)).toEqual(['Declarator']);
});

it('requires both event and match modules for the Declarator workspace', () => {
  expect(setup(['results.submit'], ['matches']).nav()).toEqual([]);
});

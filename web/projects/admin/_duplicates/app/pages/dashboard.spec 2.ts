import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { NEVER, of, throwError } from 'rxjs';

import type { AdminDashboard } from '@shared/models';
import { AUTH_CONFIG, AdminApi, AuthStore, RealtimeService } from '@shared/services';
import { DashboardPage } from './dashboard';

function render(dashboard: () => unknown) {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
      { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } },
      { provide: RealtimeService, useValue: { connect: () => undefined, on: () => NEVER } },
      { provide: AdminApi, useValue: { dashboard } },
    ],
  });
  TestBed.inject(AuthStore).acceptSession({
    access_token: 't', token_type: 'bearer', expires_in: 900,
    user: { permissions: ['dashboard.view'], must_change_password: false, account_type: 'agent', full_name: 'Agent', tenant: { name: 'T' } },
  } as never);
  const fixture = TestBed.createComponent(DashboardPage);
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

const base: AdminDashboard = {
  users_by_type: { player: 3 }, active_users_7d: null, events_by_status: null, matches_in_progress: null, results_7d: null,
  points: null, activity: null, recent_audit: null, modules: [],
};

describe('DashboardPage values', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('omits figures the API did not return instead of showing zero', () => {
    const text = render(() => of(base)).textContent ?? '';
    expect(text).toContain('3');
    expect(text).not.toContain('signed in this week');
  });

  it('shows a real zero when the API reports zero', () => {
    const text = render(() => of({ ...base, active_users_7d: 0 })).textContent ?? '';
    expect(text).toContain('0 signed in this week');
  });

  it('shows an error with retry, not placeholder numbers, when the request fails', () => {
    const el = render(() => throwError(() => ({ status: 503, error: { error: { message: 'Service unavailable' } } })));
    expect(el.querySelector('app-error-state')).not.toBeNull();
    expect(el.querySelector('app-stat-card')).toBeNull();
  });
});

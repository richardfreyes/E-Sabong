import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRouteSnapshot, Router, RouterStateSnapshot, UrlTree, Route, provideRouter } from '@angular/router';

import { AUTH_CONFIG, AuthStore } from '../services/auth.store';
import { BrandingService } from '../services/branding.service';
import { authGuard, permissionGuard, moduleGuard } from './index';

function signedIn(store: AuthStore, user: object): void {
  store.acceptSession({ access_token: 't', token_type: 'bearer', expires_in: 900, user } as never);
}

describe('route guards', () => {
  const state = { url: '/users' } as RouterStateSnapshot;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login', changePasswordRoute: '/change-password' } },
      ],
    });
  });

  const run = (guard: typeof authGuard, route = {} as ActivatedRouteSnapshot) =>
    TestBed.runInInjectionContext(() => guard(route, state));

  it('sends anonymous users to sign in with a return URL', () => {
    const result = run(authGuard) as UrlTree;
    expect(TestBed.inject(Router).serializeUrl(result)).toBe('/login?returnUrl=%2Fusers');
  });

  it('forces a password change before anything else', () => {
    signedIn(TestBed.inject(AuthStore), { permissions: [], must_change_password: true });
    const result = run(authGuard) as UrlTree;
    expect(TestBed.inject(Router).serializeUrl(result)).toBe('/change-password');
  });

  it('allows routes when any listed permission is held', () => {
    signedIn(TestBed.inject(AuthStore), { permissions: ['users.view_downline'], must_change_password: false });
    const route = { data: { permissions: ['users.view_all', 'users.view_downline'] } } as unknown as ActivatedRouteSnapshot;
    expect(run(permissionGuard, route)).toBe(true);
    const denied = { data: { permissions: ['roles.manage'] } } as unknown as ActivatedRouteSnapshot;
    expect(TestBed.inject(Router).serializeUrl(run(permissionGuard, denied) as UrlTree)).toBe('/forbidden');
  });
});


it('requires every module listed by the Declarator route', () => {
  const enabled = new Set(['events']);
  TestBed.configureTestingModule({providers:[provideRouter([]),{provide:BrandingService,useValue:{hasModule:(key:string)=>enabled.has(key)}}]});
  const route = {data:{modules:['events','matches']}} as Route;
  const denied = TestBed.runInInjectionContext(()=>moduleGuard(route,[],{} as never)) as UrlTree;
  expect(TestBed.inject(Router).serializeUrl(denied)).toBe('/');
  enabled.add('matches');
  expect(TestBed.runInInjectionContext(()=>moduleGuard(route,[],{} as never))).toBe(true);
});

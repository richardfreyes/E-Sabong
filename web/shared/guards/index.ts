import { inject } from '@angular/core';
import { CanActivateFn, CanMatchFn, Router } from '@angular/router';

import type { ModuleKey } from '@shared/models';
import { AuthStore } from '../services/auth.store';
import { BrandingService } from '../services/branding.service';

/** Requires a signed-in user; sends accounts with temporary passwords to change it first. */
export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthStore);
  const router = inject(Router);
  if (!auth.isAuthenticated()) {
    return router.createUrlTree([auth.loginRoute], { queryParams: { returnUrl: state.url } });
  }
  const changeRoute = auth.changePasswordRoute;
  if (changeRoute && (auth.user() as { must_change_password?: boolean } | null)?.must_change_password && !state.url.startsWith(changeRoute)) {
    return router.createUrlTree([changeRoute]);
  }
  return true;
};

export const guestGuard: CanActivateFn = () => {
  const auth = inject(AuthStore);
  return auth.isAuthenticated() ? inject(Router).createUrlTree(['/']) : true;
};

/** Route data: `{ permissions: [...] }` — any one of them grants access. */
export const permissionGuard: CanActivateFn = (route) => {
  const auth = inject(AuthStore);
  const needed = (route.data['permissions'] as string[] | undefined) ?? [];
  if (!needed.length || auth.hasAnyPermission(...needed)) return true;
  return inject(Router).createUrlTree(['/forbidden']);
};

/** Route data: `{ module: 'points' }` — hides routes for modules the tenant has disabled. */
export const moduleGuard: CanMatchFn = (route) => {
  const module = route.data?.['module'] as ModuleKey | undefined;
  const modules = (route.data?.['modules'] as ModuleKey[] | undefined) ?? (module ? [module] : []);
  if (!modules.length) return true;
  const branding = inject(BrandingService);
  return modules.every(key => branding.hasModule(key)) ? true : inject(Router).createUrlTree(['/']);
};

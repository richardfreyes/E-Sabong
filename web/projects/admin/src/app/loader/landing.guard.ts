import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import type { TenantProfile } from '@shared/models';
import { AuthStore } from '@shared/services';

/** Loaders land on their own dashboard. Other accounts are unchanged. */
export const loaderLandingGuard: CanActivateFn = () => {
  const auth = inject<AuthStore<TenantProfile>>(AuthStore);
  return auth.user()?.account_type === 'loader' ? inject(Router).createUrlTree(['/loader']) : true;
};

/** The Loader dashboard is only for Loader accounts, even if another role holds the permission. */
export const loaderOnlyGuard: CanActivateFn = () => {
  const auth = inject<AuthStore<TenantProfile>>(AuthStore);
  return auth.user()?.account_type === 'loader' ? true : inject(Router).createUrlTree(['/']);
};

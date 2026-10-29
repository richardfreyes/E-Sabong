import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import type { TenantProfile } from '@shared/models';
import { AuthStore, BrandingService } from '@shared/services';

/** Keep the role's default destination consistent with permission/module guards. */
export const declaratorLandingGuard: CanActivateFn = () => {
  const auth = inject<AuthStore<TenantProfile>>(AuthStore);
  if (auth.user()?.account_type !== 'declarator') return true;
  const branding = inject(BrandingService);
  const allowed = auth.hasAnyPermission('results.submit', 'results.approve')
    && branding.hasModule('events') && branding.hasModule('matches');
  return inject(Router).createUrlTree([allowed ? '/declarator' : '/forbidden']);
};

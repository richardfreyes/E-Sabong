import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import type { TenantProfile } from '@shared/models';
import { AuthStore, BrandingService } from '@shared/services';

export const isStreamOperator = (user: TenantProfile | null | undefined): boolean => user?.account_type === 'stream_operator';

/**
 * Stream Operator accounts never enter the administration layout: every admin URL sends them to
 * their own dashboard. (The API refuses everything outside /auth and /operator for them too.)
 */
export const keepStreamOperatorsOut: CanActivateFn = () => {
  const auth = inject<AuthStore<TenantProfile>>(AuthStore);
  return isStreamOperator(auth.user()) ? inject(Router).createUrlTree(['/operator']) : true;
};

/** The Operator dashboard: Stream Operators and administrators (streams.operate). */
export const operatorGuard: CanActivateFn = () => {
  const auth = inject<AuthStore<TenantProfile>>(AuthStore);
  return auth.hasPermission('streams.operate') ? true : inject(Router).createUrlTree(['/forbidden']);
};

/** System capability checked again by every streaming API endpoint. */
export const streamProviderGuard: CanActivateFn = async () => {
  const branding = inject(BrandingService);
  const router = inject(Router);
  const auth = inject<AuthStore<TenantProfile>>(AuthStore);
  // Load authenticated branding: shared domains resolve the signed-in workspace.
  await branding.load();
  return branding.config()?.is_global_stream_provider === true &&
    auth.hasAnyPermission('settings.manage', 'streams.operate')
    ? true : router.createUrlTree(['/forbidden']);
};

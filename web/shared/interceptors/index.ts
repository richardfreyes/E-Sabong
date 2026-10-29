import { HttpInterceptorFn, HttpRequest } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, switchMap, throwError } from 'rxjs';

import { AuthStore } from '../services/auth.store';
import { toApiError } from '../services/api-client';
import { TenantContext } from '../services/tenant-context';

const isApi = (req: HttpRequest<unknown>) => req.url.startsWith('/api/');

/** Adds the bearer token and, on an expired token, refreshes once and retries. */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (!isApi(req)) return next(req);
  const auth = inject(AuthStore);
  const withToken = (token: string | null) =>
    token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}`, 'X-Requested-With': 'XMLHttpRequest' } }) : req;

  return next(withToken(auth.token())).pipe(
    catchError((err: unknown) => {
      // Runs after errorInterceptor has normalised the failure to ApiError.
      const apiError = toApiError(err);
      if (apiError.status !== 401 || !auth.token()) return throwError(() => apiError);
      const code = apiError.code;
      if (code !== 'token_expired' && code !== 'session_revoked' && code !== 'invalid_token') return throwError(() => apiError);
      return auth.refresh().pipe(
        switchMap((token) => {
          if (!token) {
            auth.sessionExpired();
            return throwError(() => apiError);
          }
          return next(withToken(token));
        }),
      );
    }),
  );
};

/** Development only: forwards the selected tenant (see TenantContext). */
export const tenantInterceptor: HttpInterceptorFn = (req, next) => {
  const slug = inject(TenantContext).devSlug();
  return slug && isApi(req) ? next(req.clone({ setHeaders: { 'X-Tenant-Slug': slug } })) : next(req);
};

/** Converts HTTP failures into ApiError so components handle one error type. */
export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthStore);
  const router = inject(Router);
  return next(req).pipe(
    catchError((err: unknown) => {
      const apiError = toApiError(err);
      const changeRoute = auth.changePasswordRoute;
      if (apiError.code === 'password_change_required' && changeRoute) {
        void auth.reloadProfile().then(() => router.navigateByUrl(changeRoute));
      }
      return throwError(() => apiError);
    }),
  );
};

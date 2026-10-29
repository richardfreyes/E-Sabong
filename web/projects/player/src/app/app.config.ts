import { ApplicationConfig, inject, provideAppInitializer, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding, withInMemoryScrolling } from '@angular/router';

import { ThemeService, provideAppCore } from '@shared/services';
import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding(), withInMemoryScrolling({ scrollPositionRestoration: 'top' })),
    provideAppCore({
      auth: { basePath: '/auth', loginRoute: '/login', changePasswordRoute: '/change-password' },
      tenantBranding: true,
    }),
    // The player portal is dark only. Setting the signal (not setMode) leaves the
    // stored preference, shared with the admin portal on the same origin, untouched.
    provideAppInitializer(() => inject(ThemeService).mode.set('dark')),
  ],
};

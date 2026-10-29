import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding, withInMemoryScrolling } from '@angular/router';

import { provideAppCore } from '@shared/services';
import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding(), withInMemoryScrolling({ scrollPositionRestoration: 'top' })),
    provideAppCore({
      auth: { basePath: '/auth', loginRoute: '/login', changePasswordRoute: '/change-password' },
      tenantBranding: true,
    }),
  ],
};

import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { EnvironmentProviders, Injector, Provider, computed, effect, inject, isDevMode, provideAppInitializer, runInInjectionContext, untracked } from '@angular/core';
import { MatIconRegistry } from '@angular/material/icon';
import { MAT_FORM_FIELD_DEFAULT_OPTIONS } from '@angular/material/form-field';
import { TitleStrategy } from '@angular/router';

import { authInterceptor, errorInterceptor, tenantInterceptor } from '../interceptors';
import { AUTH_CONFIG, AuthConfig, AuthStore } from './auth.store';
import { BrandingService } from './branding.service';
import { ThemeService } from './theme.service';
import { APP_TITLE, BrandedTitleStrategy } from './title-strategy';

export interface AppCoreOptions {
  auth: AuthConfig;
  /** Tenant-facing apps load white-label branding for the current host. */
  tenantBranding: boolean;
  /** Title suffix when no tenant branding applies. */
  appTitle?: string;
}

/** Providers shared by the three portals. */
export function provideAppCore(options: AppCoreOptions): (Provider | EnvironmentProviders)[] {
  const interceptors = [...(isDevMode() && options.tenantBranding ? [tenantInterceptor] : []), authInterceptor, errorInterceptor];
  return [
    provideHttpClient(withInterceptors(interceptors)),
    { provide: AUTH_CONFIG, useValue: options.auth },
    { provide: MAT_FORM_FIELD_DEFAULT_OPTIONS, useValue: { appearance: 'outline' } },
    { provide: TitleStrategy, useClass: BrandedTitleStrategy },
    ...(options.appTitle ? [{ provide: APP_TITLE, useValue: options.appTitle }] : []),
    provideAppInitializer(async () => {
      inject(MatIconRegistry).setDefaultFontSetClass('material-symbols-outlined');
      inject(ThemeService);
      const auth = inject(AuthStore);
      const branding = options.tenantBranding ? inject(BrandingService) : null;
      const injector = inject(Injector);
      // Shared-domain branding requires the restored user's tenant identity.
      await auth.restore();
      if (branding) {
        await branding.load();
        runInInjectionContext(injector, () => {
          const sessionTenant = computed(() => (auth.user() as { tenant?: { id: string } } | null)?.tenant?.id ?? null);
          let previousTenant = sessionTenant();
          effect(() => {
            const tenant = sessionTenant();
            if (tenant !== previousTenant) {
              previousTenant = tenant;
              untracked(() => void branding.load());
            }
          });
        });
      }
    }),
  ];
}

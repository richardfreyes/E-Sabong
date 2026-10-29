import { Injectable, InjectionToken, inject } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { RouterStateSnapshot, TitleStrategy } from '@angular/router';

import { BrandingService } from './branding.service';

/** Product name used when no tenant branding applies (the owner console). */
export const APP_TITLE = new InjectionToken<string>('APP_TITLE', { factory: () => 'Event Platform' });

/** "Page · Tenant app name", so route titles never replace the white-label name. */
@Injectable()
export class BrandedTitleStrategy extends TitleStrategy {
  private readonly title = inject(Title);
  private readonly branding = inject(BrandingService);
  private readonly fallback = inject(APP_TITLE);

  override updateTitle(snapshot: RouterStateSnapshot): void {
    const page = this.buildTitle(snapshot);
    const app = this.branding.config()?.branding.app_name ?? this.fallback;
    this.title.setTitle(page ? `${page} · ${app}` : app);
  }
}

import { HttpClient } from '@angular/common/http';
import { DOCUMENT } from '@angular/common';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { firstValueFrom } from 'rxjs';

import type { ModuleKey, PublicTenantConfig } from '@shared/models';
import { API_BASE, ApiError, toApiError } from './api-client';
import { ThemeService } from './theme.service';

/**
 * Loads the tenant's public configuration for the current host and applies its
 * branding (name, colours, favicon) at runtime, so one build serves every tenant.
 */
@Injectable({ providedIn: 'root' })
export class BrandingService {
  private readonly http = inject(HttpClient);
  private readonly theme = inject(ThemeService);
  private readonly title = inject(Title);
  private readonly doc = inject(DOCUMENT);

  readonly config = signal<PublicTenantConfig | null>(null);
  readonly error = signal<ApiError | null>(null);
  readonly appName = computed(() => this.config()?.branding.app_name ?? 'Event Platform');
  readonly logoUrl = computed(() => this.config()?.branding.logo_url ?? null);
  readonly suspended = computed(() => {
    const status = this.config()?.status;
    return status === 'suspended' || status === 'cancelled';
  });

  hasModule(module: ModuleKey): boolean {
    return this.config()?.modules.includes(module) ?? false;
  }

  async load(): Promise<void> {
    try {
      const cfg = await firstValueFrom(this.http.get<PublicTenantConfig>(`${API_BASE}/public/tenant`));
      this.apply(cfg);
    } catch (err) {
      this.error.set(toApiError(err));
    }
  }

  apply(cfg: PublicTenantConfig): void {
    this.config.set(cfg);
    const b = cfg.branding;
    this.theme.applyBrand({ primary: b.primary_color, secondary: b.secondary_color, background: b.background_color, surface: b.surface_color });
    this.title.setTitle(b.app_name);
    this.setFavicon(b.favicon_url || 'favicon.ico');
  }

  private setFavicon(href: string): void {
    let link = this.doc.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!link) {
      link = this.doc.createElement('link');
      link.rel = 'icon';
      this.doc.head.appendChild(link);
    }
    link.type = href.endsWith('.ico') ? 'image/x-icon' : 'image/png';
    link.href = href;
  }
}

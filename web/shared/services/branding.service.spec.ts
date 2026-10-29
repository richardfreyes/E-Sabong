import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Title } from '@angular/platform-browser';
import type { PublicTenantConfig } from '../models';
import { BrandingService } from './branding.service';

const config = (name: string, primary: string, favicon: string | null): PublicTenantConfig => ({
  id: 'tenant', slug: 'demo', name, status: 'active', timezone: 'Asia/Manila', locale: 'en', modules: ['events', 'points'], feature_flags: {}, allow_self_registration: false,
  branding: { app_name: name, primary_color: primary, secondary_color: '#2563EB', background_color: '#090B10', surface_color: '#121620',
    logo_url: '/api/media/logo.png', favicon_url: favicon, login_title: null, login_subtitle: null, login_background_url: null,
    contact_email: null, contact_phone: null, contact_address: null, footer_text: null },
});

describe('BrandingService runtime application', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('style');
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
  });

  it('updates name, logo, title, favicon and Material accent without restarting the app', () => {
    const branding = TestBed.inject(BrandingService);
    branding.apply(config('First workspace', '#0F766E', '/api/media/first.png'));
    const firstAccent = document.documentElement.style.getPropertyValue('--mat-sys-primary');
    branding.apply(config('Second workspace', '#7C3AED', '/api/media/second.png'));
    expect(branding.appName()).toBe('Second workspace');
    expect(branding.logoUrl()).toBe('/api/media/logo.png');
    expect(TestBed.inject(Title).getTitle()).toBe('Second workspace');
    expect(document.querySelector('link[rel="icon"]')?.getAttribute('href')).toBe('/api/media/second.png');
    expect(document.documentElement.style.getPropertyValue('--mat-sys-primary')).not.toBe(firstAccent);
    expect(branding.hasModule('events')).toBe(true);
    expect(branding.hasModule('reports')).toBe(false);
  });

  it('restores the fallback favicon when a tenant removes its custom icon', () => {
    const branding = TestBed.inject(BrandingService);
    branding.apply(config('Workspace', '#0F766E', '/api/media/custom.png'));
    branding.apply(config('Workspace', '#0F766E', null));
    const icon = document.querySelector('link[rel="icon"]');
    expect(icon?.getAttribute('href')).toBe('favicon.ico');
    expect(icon?.getAttribute('type')).toBe('image/x-icon');
  });
});

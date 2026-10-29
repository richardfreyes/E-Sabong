import { TestBed } from '@angular/core/testing';

import { ThemeService, contrastRatio } from './theme.service';

describe('ThemeService', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('style');
  });

  it('writes light and dark Material tokens from tenant colours', () => {
    const theme = TestBed.inject(ThemeService);
    theme.applyBrand({ primary: '#0F766E', secondary: '#7C3AED', background: '#F8FAF9' });
    const style = document.documentElement.style;
    expect(style.getPropertyValue('--mat-sys-primary')).toMatch(/^light-dark\(#[0-9a-f]{6}, #[0-9a-f]{6}\)$/);
    expect(style.getPropertyValue('--mat-sys-on-primary')).toContain('light-dark(');
    expect(style.getPropertyValue('--app-bg')).toContain('#F8FAF9');
  });

  it('persists the chosen mode and toggles the root class', () => {
    const theme = TestBed.inject(ThemeService);
    theme.setMode('dark');
    TestBed.tick();
    expect(localStorage.getItem('esb.theme')).toBe('dark');
    expect(document.documentElement.classList.contains('theme-dark')).toBe(true);
    theme.toggle();
    TestBed.tick();
    expect(theme.mode()).toBe('light');
    expect(document.documentElement.classList.contains('theme-light')).toBe(true);
  });

  it('uses Midnight dark mode when no display preference is stored', () => {
    const theme = TestBed.inject(ThemeService);
    TestBed.tick();
    expect(theme.mode()).toBe('dark');
    expect(document.documentElement.classList.contains('theme-dark')).toBe(true);
  });

  it('keeps dark surfaces and semantic outcomes independent of tenant accents', () => {
    const theme = TestBed.inject(ThemeService);
    const style = document.documentElement.style;
    style.setProperty('--meron', '#DC2626');
    style.setProperty('--wala', '#2563EB');
    theme.applyBrand({ primary: '#FF00FF', secondary: '#00FF00', background: '#FFFFFF', surface: '#FFFF00' });
    expect(style.getPropertyValue('--mat-sys-surface')).toContain('var(--surface)');
    expect(style.getPropertyValue('--mat-sys-on-surface')).toContain('var(--text-primary)');
    expect(style.getPropertyValue('--brand-background-dark')).toBe('');
    expect(style.getPropertyValue('--brand-surface-dark')).toBe('');
    expect(style.getPropertyValue('--meron')).toBe('#DC2626');
    expect(style.getPropertyValue('--wala')).toBe('#2563EB');
  });

  it.each(['#FFFFFF', '#000000', '#FF0000', '#00FF00', '#0000FF', '#F5C542'])('maps %s to accessible primary control colors in both modes', primary => {
    const theme = TestBed.inject(ThemeService);
    theme.applyBrand({ primary, secondary: '#2563EB' });
    const values = (token: string) => [...document.documentElement.style.getPropertyValue(token).matchAll(/#[0-9a-f]{6}/gi)].map(match => match[0]);
    const foreground = values('--mat-sys-on-primary');
    const background = values('--mat-sys-primary');
    expect(contrastRatio(foreground[0], background[0])).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(foreground[1], background[1])).toBeGreaterThanOrEqual(4.5);
  });

  it('accepts safe dark surface variations and clears them when the next brand removes them', () => {
    const theme = TestBed.inject(ThemeService);
    const style = document.documentElement.style;
    theme.applyBrand({ primary: '#0F766E', secondary: '#7C3AED', background: '#080D18', surface: '#151C2A' });
    expect(style.getPropertyValue('--brand-background-dark')).toBe('#080D18');
    expect(style.getPropertyValue('--brand-surface-dark')).toBe('#151C2A');
    theme.applyBrand({ primary: '#F5C542', secondary: '#2563EB' });
    expect(style.getPropertyValue('--brand-background-dark')).toBe('');
    expect(style.getPropertyValue('--brand-surface-dark')).toBe('');
    expect(style.getPropertyValue('--app-bg')).toBe('var(--background)');
  });

});

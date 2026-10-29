import { DOCUMENT } from '@angular/common';
import { Injectable, computed, effect, inject, signal } from '@angular/core';
import {
  DynamicScheme,
  Hct,
  MaterialDynamicColors,
  TonalPalette,
  Variant,
  argbFromHex,
  hexFromArgb,
} from '@material/material-color-utilities';

export type ThemeMode = 'light' | 'dark' | 'system';
export const DEFAULT_BRAND_COLORS: Readonly<Required<BrandColors>> = { primary: '#F5C542', secondary: '#2563EB', background: '#090B10', surface: '#121620' };

const STORAGE_KEY = 'esb.theme';

// Material system tokens regenerated from the tenant's colours.
const TOKENS: [string, keyof typeof MaterialDynamicColors][] = [
  ['primary', 'primary'],
  ['on-primary', 'onPrimary'],
  ['primary-container', 'primaryContainer'],
  ['on-primary-container', 'onPrimaryContainer'],
  ['inverse-primary', 'inversePrimary'],
  ['secondary', 'secondary'],
  ['on-secondary', 'onSecondary'],
  ['secondary-container', 'secondaryContainer'],
  ['on-secondary-container', 'onSecondaryContainer'],
  ['tertiary', 'tertiary'],
  ['on-tertiary', 'onTertiary'],
  ['tertiary-container', 'tertiaryContainer'],
  ['on-tertiary-container', 'onTertiaryContainer'],
  ['surface', 'surface'],
  ['surface-dim', 'surfaceDim'],
  ['surface-bright', 'surfaceBright'],
  ['surface-container-lowest', 'surfaceContainerLowest'],
  ['surface-container-low', 'surfaceContainerLow'],
  ['surface-container', 'surfaceContainer'],
  ['surface-container-high', 'surfaceContainerHigh'],
  ['surface-container-highest', 'surfaceContainerHighest'],
  ['surface-variant', 'surfaceVariant'],
  ['on-surface', 'onSurface'],
  ['on-surface-variant', 'onSurfaceVariant'],
  ['inverse-surface', 'inverseSurface'],
  ['inverse-on-surface', 'inverseOnSurface'],
  ['outline', 'outline'],
  ['outline-variant', 'outlineVariant'],
  ['surface-tint', 'surfaceTint'],
  ['background', 'background'],
  ['on-background', 'onBackground'],
];

export interface BrandColors {
  primary: string;
  secondary: string;
  background?: string;
  surface?: string;
}

function scheme(colors: BrandColors, isDark: boolean): DynamicScheme {
  const primary = Hct.fromInt(argbFromHex(colors.primary));
  const secondary = Hct.fromInt(argbFromHex(colors.secondary));
  return new DynamicScheme({
    sourceColorHct: primary,
    // Tonal-spot keeps containers soft; the tenant's colours still drive every palette.
    variant: Variant.TONAL_SPOT,
    contrastLevel: 0.5,
    isDark,
    primaryPalette: TonalPalette.fromHct(primary),
    secondaryPalette: TonalPalette.fromHueAndChroma(secondary.hue, Math.max(secondary.chroma, 24)),
    tertiaryPalette: TonalPalette.fromHct(secondary),
    neutralPalette: TonalPalette.fromHueAndChroma(primary.hue, 6),
    neutralVariantPalette: TonalPalette.fromHueAndChroma(primary.hue, 10),
  });
}

/** Dark surfaces stay neutral even when a tenant changes its accent palette. */
const DARK_TOKENS: Record<string, string> = {
  surface: '--surface', 'surface-dim': '--background', 'surface-bright': '--surface-elevated',
  'surface-container-lowest': '--surface', 'surface-container-low': '--surface',
  'surface-container': '--surface-elevated', 'surface-container-high': '--surface-elevated',
  'surface-container-highest': '--surface-hover', 'surface-variant': '--surface-elevated',
  'on-surface': '--text-primary', 'on-surface-variant': '--text-secondary',
  'inverse-surface': '--text-primary', 'inverse-on-surface': '--background',
  outline: '--control-border', 'outline-variant': '--border', background: '--background', 'on-background': '--text-primary',
};
const HEX = /^#[0-9a-f]{6}$/i;
const validHex = (value: string | undefined): value is string => !!value && HEX.test(value);

/** WCAG relative luminance, also used to gate approved dark surface variations. */
export function relativeLuminance(hex: string): number {
  const channels = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}
export function contrastRatio(a: string, b: string): number {
  const values = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly doc = inject(DOCUMENT);
  private readonly media =
    typeof this.doc.defaultView?.matchMedia === 'function' ? this.doc.defaultView.matchMedia('(prefers-color-scheme: dark)') : undefined;
  private readonly systemDark = signal(this.media?.matches ?? false);

  readonly mode = signal<ThemeMode>(this.readStoredMode());
  readonly isDark = computed(() => (this.mode() === 'system' ? this.systemDark() : this.mode() === 'dark'));

  constructor() {
    this.media?.addEventListener('change', (e) => this.systemDark.set(e.matches));
    effect(() => {
      const root = this.doc.documentElement;
      root.classList.toggle('theme-dark', this.isDark());
      root.classList.toggle('theme-light', !this.isDark());
    });
  }

  setMode(mode: ThemeMode): void {
    this.mode.set(mode);
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      /* storage unavailable: preference lasts for this page only */
    }
  }

  toggle(): void {
    this.setMode(this.isDark() ? 'light' : 'dark');
  }

  /** Brand accents are contrast-mapped; dark neutrals and outcome colors remain independent. */
  applyBrand(colors: BrandColors): void {
    const primary = validHex(colors.primary) ? colors.primary : DEFAULT_BRAND_COLORS.primary;
    const secondary = validHex(colors.secondary) ? colors.secondary : DEFAULT_BRAND_COLORS.secondary;
    const safe = { ...colors, primary, secondary };
    const light = scheme(safe, false);
    const dark = scheme(safe, true);
    const style = this.doc.documentElement.style;
    for (const [token, key] of TOKENS) {
      const color = MaterialDynamicColors[key] as { getArgb(s: DynamicScheme): number };
      const darkValue = DARK_TOKENS[token] ? `var(${DARK_TOKENS[token]})` : hexFromArgb(color.getArgb(dark));
      style.setProperty(`--mat-sys-${token}`, `light-dark(${hexFromArgb(color.getArgb(light))}, ${darkValue})`);
    }
    for (const [key, value] of [['background', colors.background], ['surface', colors.surface]] as const) {
      // Legacy light branding remains available in light mode. Bright custom colors
      // cannot turn a Midnight surface into a low-contrast background.
      if (validHex(value) && relativeLuminance(value) <= 0.035) style.setProperty(`--brand-${key}-dark`, value);
      else style.removeProperty(`--brand-${key}-dark`);
    }
    style.setProperty('--app-bg', validHex(colors.background) ? `light-dark(${colors.background}, var(--background))` : 'var(--background)');
  }

  private readStoredMode(): ThemeMode {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return v === 'light' || v === 'dark' || v === 'system' ? v : 'dark';
    } catch {
      return 'dark';
    }
  }
}

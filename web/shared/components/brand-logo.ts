import { ChangeDetectionStrategy, Component, Injectable, computed, inject, input } from '@angular/core';

export type BrandLogoVariant = 'static' | 'compact' | 'reveal' | 'loop';

const BRAND_FILES: Record<BrandLogoVariant, string> = {
  static: 'assets/brand/e-sabong-logo.svg',
  compact: 'assets/brand/e-sabong-logo-compact.svg',
  reveal: 'assets/brand/e-sabong-logo-reveal.svg',
  loop: 'assets/brand/e-sabong-logo-loop.svg',
};

/** Length of the reveal's entrance sequence; the SVG idles on its final frame afterwards. */
export const BRAND_REVEAL_MS = 3200;

/** Tracks whether the one-time logo reveal has already played in this browser session. */
@Injectable({ providedIn: 'root' })
export class BrandRevealService {
  private static readonly key = 'es-splash';

  /** True only for the first caller in a browser session; the flag is stored immediately. */
  claim(): boolean {
    try {
      if (sessionStorage.getItem(BrandRevealService.key)) return false;
      sessionStorage.setItem(BrandRevealService.key, '1');
      return true;
    } catch {
      return false;
    }
  }
}

/**
 * E-Sabong logo for every portal. The animated variants are CSS-only SVGs rendered through
 * <img>, and they already show the final static frame under prefers-reduced-motion.
 * A tenant's own logo, when configured, is passed as `src` and replaces the artwork.
 */
@Component({
  selector: 'app-brand-logo',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[style.--brand-logo-size]': 'size() + "px"' },
  styles: `
    :host { display: inline-block; flex: none; width: var(--brand-logo-size); max-width: 100%; line-height: 0; }
    img { display: block; width: 100%; height: auto; aspect-ratio: 1; object-fit: contain; }
    /* The artwork is drawn for dark surfaces; light mode sits it on a midnight tile. */
    :host-context(.theme-light) .artwork { border-radius: 18%; background: var(--brand-logo-tile); }
  `,
  template: `<img [class.artwork]="!src()" [src]="src() || file()" [alt]="alt()" [width]="size()" [height]="size()" decoding="async" />`,
})
export class BrandLogoComponent {
  readonly variant = input<BrandLogoVariant>('static');
  readonly size = input(48);
  readonly alt = input('E-Sabong');
  readonly src = input<string | null>(null);
  /** Reveal plays once per session; set false when the caller has already claimed it. */
  readonly once = input(true);

  private readonly reveal = inject(BrandRevealService);
  private revealPlays?: boolean;

  readonly resolvedVariant = computed<BrandLogoVariant>(() => {
    const variant = this.variant();
    if (variant !== 'reveal' || !this.once()) return variant;
    this.revealPlays ??= this.reveal.claim();
    return this.revealPlays ? 'reveal' : 'static';
  });
  readonly file = computed(() => BRAND_FILES[this.resolvedVariant()]);
}

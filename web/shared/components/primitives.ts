import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { AbstractControl } from '@angular/forms';

/** Placeholder block shown while content loads. */
@Component({
  selector: 'app-skeleton',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="block rounded-md bg-surface-container-highest" [style.width]="width()" [style.height]="height()"
    style="animation: skeleton-pulse 1.4s ease-in-out infinite" aria-hidden="true"></span>`,
})
export class SkeletonComponent {
  readonly width = input('100%');
  readonly height = input('1rem');
}

@Component({
  selector: 'app-page-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    header { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 28px; }
    .heading { min-width: 0; flex: 1 1 220px; }
    .eyebrow { margin: 0 0 10px; color: var(--text-secondary); font-size: 0.65rem; font-weight: 600; letter-spacing: 0.13em; text-transform: uppercase; }
    h1 { margin: 0; color: var(--text-primary); font: 650 clamp(1.4rem, 1.2rem + 0.7vw, 1.8rem)/1.25 var(--font-sans); letter-spacing: -0.04em; }
    .subtitle { margin: 8px 0 0; color: var(--text-secondary); font-size: 0.8rem; line-height: 1.7; max-width: 72ch; }
    .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    @media(max-width:639px) { header { gap: 14px; margin-bottom: 22px; } .actions { width: 100%; } }
  `,
  template: `
    <header><div class="heading"><ng-content select="[breadcrumb]" />
      @if (eyebrow()) { <p class="eyebrow">{{ eyebrow() }}</p> }
      <h1>{{ title() }}</h1>@if (subtitle()) { <p class="subtitle">{{ subtitle() }}</p> }
    </div><div class="actions"><ng-content /></div></header>
  `,
})
export class PageHeaderComponent {
  readonly eyebrow = input<string | null>(null);
  readonly title = input.required<string>();
  readonly subtitle = input<string | null>(null);
}

@Component({
  selector: 'app-stat-card',
  imports: [MatIconModule, SkeletonComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; min-width: 0; }
    section { display: flex; flex-direction: column; gap: 18px; height: 100%; min-width: 0; padding: 20px; border: 1px solid var(--border); border-radius: var(--radius-card); background: var(--surface); box-shadow: var(--shadow-card); transition: border-color var(--duration-fast), transform var(--duration-fast); }
    section:hover { border-color: color-mix(in srgb, var(--text-secondary) 35%, var(--border)); transform: translateY(-1px); }
    .top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    h2 { margin: 0; color: var(--text-secondary); font: 500 0.75rem/1.65 var(--font-sans); }
    .icon { width: 34px; height: 34px; display: grid; place-items: center; flex: none; border-radius: 10px; background: var(--surface-elevated); color: var(--text-secondary); }
    .icon mat-icon { width: 18px; height: 18px; font-size: 18px; }
    .accent .icon { background: color-mix(in srgb, var(--mat-sys-primary) 10%, var(--surface)); color: var(--mat-sys-primary); }
    .success .icon { background: color-mix(in srgb, var(--success) 10%, var(--surface)); color: var(--success-text); }
    .warning .icon { background: color-mix(in srgb, var(--warning) 10%, var(--surface)); color: var(--warning-text); }
    .value { margin: 0; font: 650 clamp(1.5rem, 1.2rem + 0.6vw, 1.9rem)/1.2 var(--font-sans); color: var(--text-primary); letter-spacing: -0.04em; font-variant-numeric: tabular-nums; }
    .hint { margin: auto 0 0; color: var(--text-secondary); font-size: 0.7rem; line-height: 1.7; }
    @media(max-width:639px) { section { padding: 16px; gap: 14px; } }
  `,
  template: `
    <section [attr.aria-busy]="loading()" [class]="tone()"><div class="top"><h2>{{ label() }}</h2>
      @if (icon()) { <span class="icon"><mat-icon aria-hidden="true">{{ icon() }}</mat-icon></span> }
    </div>
    @if (loading()) { <app-skeleton width="65%" height="2rem" /> } @else { <p class="value">{{ value() ?? '—' }}</p> }
    @if (hint()) { <p class="hint">{{ hint() }}</p> }
    </section>
  `,
})
export class StatCardComponent {
  readonly tone = input<'neutral' | 'accent' | 'success' | 'warning'>('neutral');
  readonly label = input.required<string>();
  readonly value = input<string | number | null>(null);
  readonly icon = input<string | null>(null);
  readonly hint = input<string | null>(null);
  readonly loading = input(false);
}

@Component({
  selector: 'app-empty-state',
  imports: [MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col items-center gap-2 px-4 py-12 text-center" role="status">
      <mat-icon class="!h-12 !w-12 !text-5xl text-on-surface-variant" aria-hidden="true">{{ icon() }}</mat-icon>
      <p class="m-0 text-base font-medium text-on-surface">{{ title() }}</p>
      @if (message()) {
        <p class="m-0 max-w-md text-sm text-on-surface-variant">{{ message() }}</p>
      }
      <div class="mt-2"><ng-content /></div>
    </div>
  `,
})
export class EmptyStateComponent {
  readonly icon = input('inbox');
  readonly title = input('Nothing here yet');
  readonly message = input<string | null>(null);
}

@Component({
  selector: 'app-error-state',
  imports: [MatIconModule, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col items-center gap-2 px-4 py-10 text-center" role="alert">
      <mat-icon class="!h-10 !w-10 !text-4xl text-error" aria-hidden="true">error</mat-icon>
      <p class="m-0 text-base font-medium text-on-surface">Something went wrong</p>
      <p class="m-0 max-w-md text-sm text-on-surface-variant">{{ message() }}</p>
      <button mat-stroked-button type="button" class="mt-2" (click)="retry.emit()">Try again</button>
    </div>
  `,
})
export class ErrorStateComponent {
  readonly message = input('The request could not be completed.');
  readonly retry = output<void>();
}

const STATUS_TONE: Record<string, 'good' | 'warn' | 'bad' | 'info' | 'neutral' | 'live'> = {
  finalized: 'good', pending_approval: 'warn', declared: 'info', under_review: 'warn',
  active: 'good', verified: 'good', paid: 'good', completed: 'good', approved: 'good',
  trialing: 'info', scheduled: 'info', issued: 'info', provisioning: 'info', open: 'info',
  in_progress: 'live', live: 'live',
  pending: 'warn', past_due: 'warn', draft: 'neutral',
  suspended: 'bad', expired: 'bad', revoked: 'bad', failed: 'bad', rejected: 'bad', overdue: 'bad',
  cancelled: 'neutral', void: 'neutral', decommissioned: 'neutral',
};

const STATUS_ICON: Record<string, string> = {
  good: 'check_circle', warn: 'schedule', bad: 'block', info: 'info', neutral: 'radio_button_unchecked', live: 'sensors',
};

/** Status label with icon; colour is never the only signal. */
@Component({
  selector: 'app-status-chip',
  imports: [MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: inline-flex; }
    .chip { display: inline-flex; align-items: center; gap: 0.25rem; border-radius: 999px; padding: 0.125rem 0.625rem 0.125rem 0.375rem;
      font-size: 0.68rem; font-weight: 500; line-height: 1.25rem; white-space: nowrap; border: 1px solid var(--border); text-transform: capitalize; }
    .chip mat-icon { width: 16px; height: 16px; font-size: 16px; }
    .good { background: color-mix(in srgb, var(--success) 12%, var(--surface)); color: var(--success-text); border: 1px solid color-mix(in srgb, var(--success) 25%, var(--border)); }
    .warn { background: color-mix(in srgb, var(--warning) 12%, var(--surface)); color: var(--warning-text); border: 1px solid color-mix(in srgb, var(--warning) 25%, var(--border)); }
    .bad { background: color-mix(in srgb, var(--error) 12%, var(--surface)); color: var(--error-text); border: 1px solid color-mix(in srgb, var(--error) 25%, var(--border)); }
    .info { background: color-mix(in srgb, var(--wala) 12%, var(--surface)); color: var(--wala-text); border: 1px solid color-mix(in srgb, var(--wala) 25%, var(--border)); }
    .live { background: color-mix(in srgb, var(--success) 12%, var(--surface)); color: var(--success-text); border: 1px solid color-mix(in srgb, var(--success) 25%, var(--border)); }
    .neutral { background: var(--surface-elevated); color: var(--cancelled-text); }
  `,
  template: `<span class="chip" [class]="tone()"><mat-icon aria-hidden="true">{{ icon() }}</mat-icon>{{ label() }}</span>`,
})
export class StatusChipComponent {
  readonly status = input.required<string>();
  readonly text = input<string | null>(null);
  readonly tone = computed(() => STATUS_TONE[this.status()] ?? 'neutral');
  readonly icon = computed(() => STATUS_ICON[this.tone()]);
  readonly label = computed(() => this.text() ?? this.status().replace(/_/g, ' '));
}

const MESSAGES: Record<string, (e: Record<string, unknown>) => string> = {
  required: () => 'This field is required',
  email: () => 'Enter a valid email address',
  minlength: (e) => `Use at least ${(e as { requiredLength: number }).requiredLength} characters`,
  maxlength: (e) => `Use at most ${(e as { requiredLength: number }).requiredLength} characters`,
  min: (e) => `Must be at least ${(e as { min: number }).min}`,
  max: (e) => `Must be at most ${(e as { max: number }).max}`,
  pattern: () => 'The format is not valid',
  server: (e) => String(e),
  mismatch: () => 'Passwords do not match',
};

/** Renders the first validation message of a control (use inside <mat-error>). */
@Component({
  selector: 'app-field-error',
  changeDetection: ChangeDetectionStrategy.Default,
  template: `{{ message }}`,
})
export class FieldErrorComponent {
  readonly control = input.required<AbstractControl | null>();

  get message(): string {
    const errors = this.control()?.errors;
    if (!errors) return '';
    const [key, value] = Object.entries(errors)[0];
    return (MESSAGES[key] ?? (() => 'Invalid value'))(value as Record<string, unknown>);
  }
}

/** Shows a one-time secret (temporary password, license key) with a copy button. */
@Component({
  selector: 'app-secret-reveal',
  imports: [MatButtonModule, MatIconModule, MatTooltipModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="rounded-xl border border-outline-variant bg-surface-container p-3">
      <p class="m-0 mb-2 text-sm text-on-surface-variant">{{ label() }}</p>
      <div class="flex items-center gap-2">
        <code class="min-w-0 flex-1 break-all rounded-md bg-surface-container-highest px-2 py-1 font-mono text-sm">{{ value() }}</code>
        <button mat-icon-button type="button" (click)="copy()" matTooltip="Copy" aria-label="Copy to clipboard">
          <mat-icon>{{ copied ? 'check' : 'content_copy' }}</mat-icon>
        </button>
      </div>
      <p class="m-0 mt-2 text-xs text-on-surface-variant">This is shown once. Store it securely before closing.</p>
    </div>
  `,
})
export class SecretRevealComponent {
  readonly label = input('Secret');
  readonly value = input.required<string>();
  copied = false;

  async copy(): Promise<void> {
    await navigator.clipboard.writeText(this.value());
    this.copied = true;
  }
}

@Component({
  selector: 'app-loading',
  imports: [MatProgressSpinnerModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div class="flex items-center justify-center p-10" role="status" aria-live="polite">
    <mat-spinner diameter="36" /><span class="sr-only">Loading</span></div>`,
})
export class LoadingComponent {}

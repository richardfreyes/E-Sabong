import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, input, output, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';

import { BRAND_REVEAL_MS, BrandLogoComponent, BrandRevealService } from './brand-logo';
import { FieldErrorComponent } from './primitives';

let nextId = 0;

const LANGUAGES = [
  { id: 'en', label: 'English', flag: 'assets/brand/flags/en.svg' },
  { id: 'fil', label: 'Filipino', flag: 'assets/brand/flags/ph.svg' },
  { id: 'zh', label: '中文', flag: 'assets/brand/flags/ch.svg' },
  { id: 'th', label: 'ไทย', flag: 'assets/brand/flags/th.svg' },
] as const;

/**
 * Centred sign-in card shared by every portal (owner, admin incl. declarator and agents, player).
 * It renders the form only; the page that hosts it owns the controls and the sign-in call.
 * The card: workspace logo, name and portal line, icon fields, a full-width sign-in bar,
 * a Telegram button under "Other sign-in options", language flags, and the age and jurisdiction
 * lines. Telegram and language are visual only. Optional one-time logo reveal before the form.
 * Project `[loginNotice]` content above the fields and links below the Telegram button.
 */
@Component({
  selector: 'app-login-layout',
  imports: [ReactiveFormsModule, MatIconModule, BrandLogoComponent, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './login-layout.scss',
  template: `
    <main class="login">
      <div class="card">
        <header class="masthead">
          <app-brand-logo class="hero" [variant]="intro ? 'reveal' : 'static'" [once]="false" [size]="168"
                          [src]="logoUrl()" [alt]="logoUrl() ? appName() : 'SWC'" />
          <h1 class="title">{{ appName() }}</h1>
          <p class="portal">{{ portalName() ?? 'Online Gaming Portal' }}</p>
        </header>
        @if (splashing()) {
          <button type="button" class="skip" (click)="splashing.set(false)">Skip intro</button>
        } @else {
          <div class="body">
            <ng-content select="[loginNotice]" />
            <form class="form" (submit)="onSubmit($event)" novalidate>
              <div class="field">
                <label class="sr-only" [for]="ids.user">{{ usernameLabel() }}</label>
                <div class="control">
                  <svg class="lead" aria-hidden="true" viewBox="0 0 24 24"><path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0 2c-4.4 0-8 2.2-8 5v1h16v-1c0-2.8-3.6-5-8-5Z" /></svg>
                  <input [id]="ids.user" class="input" [type]="usernameType()" [formControl]="username()" autocomplete="username"
                         [placeholder]="usernameLabel()" autocapitalize="none" spellcheck="false" [attr.aria-invalid]="invalid(username())"
                         [attr.aria-describedby]="invalid(username()) ? ids.userError : null" />
                </div>
                @if (invalid(username())) { <p class="field-error" [id]="ids.userError"><app-field-error [control]="username()" /></p> }
              </div>
              <div class="field">
                <label class="sr-only" [for]="ids.pass">Password</label>
                <div class="control">
                  <svg class="lead" aria-hidden="true" viewBox="0 0 24 24"><path d="M17 9V7a5 5 0 0 0-10 0v2H5v13h14V9h-2Zm-8-2a3 3 0 0 1 6 0v2H9V7Zm3 11a2 2 0 1 1 0-4 2 2 0 0 1 0 4Z" /></svg>
                  <input [id]="ids.pass" class="input has-toggle" [type]="showPassword() ? 'text' : 'password'" [formControl]="password()"
                         placeholder="Password" autocomplete="current-password" [attr.aria-invalid]="invalid(password())"
                         [attr.aria-describedby]="invalid(password()) ? ids.passError : null" />
                  <button type="button" class="toggle" aria-label="Show password" [attr.aria-pressed]="showPassword()" [attr.aria-controls]="ids.pass"
                          (click)="showPassword.set(!showPassword())">
                    <mat-icon aria-hidden="true">{{ showPassword() ? 'visibility' : 'visibility_off' }}</mat-icon>
                  </button>
                </div>
                @if (invalid(password())) { <p class="field-error" [id]="ids.passError"><app-field-error [control]="password()" /></p> }
              </div>
              @if (error()) { <p class="form-error" role="alert">{{ error() }}</p> }
              <button type="submit" class="submit" [disabled]="busy() || disabled()" [attr.aria-busy]="busy()">
                @if (busy()) { <span class="spinner" aria-hidden="true"></span>Signing in… }
                @else { <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M10 17l5-5-5-5v3H2v4h8v3Zm9 2h-7v2h7a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2h-7v2h7v14Z" /></svg>Sign in }
              </button>
            </form>
            <div class="signin-options">
              <p class="rule">Other sign-in options</p>
              <div class="telegram-row">
                <button type="button" class="telegram">
                  <svg aria-hidden="true" viewBox="0 0 22 22"><path d="M2.81 10.59c4.56-1.98 7.6-3.29 9.11-3.92 4.34-1.81 5.24-2.12 5.83-2.13.13 0 .42.03.6.18.16.13.2.3.23.43.02.12.04.4.02.61-.23 2.47-1.25 8.47-1.77 11.23-.22 1.17-.65 1.57-1.07 1.6-.9.09-1.59-.6-2.47-1.17-1.38-.9-2.15-1.46-3.49-2.34-1.54-1.02-.54-1.58.34-2.49.23-.24 4.23-3.88 4.3-4.21.01-.04.02-.2-.07-.28-.1-.08-.23-.05-.33-.03-.14.03-2.33 1.48-6.59 4.36-.62.43-1.19.63-1.69.62-.56-.01-1.63-.3-2.43-.56-.98-.32-1.76-.49-1.69-1.03.03-.28.42-.57 1.16-.86Z" /></svg>
                  Sign In with Telegram
                </button>
              </div>
              <!-- Portal links (register, other portal); hidden when there are none. -->
              <div class="extras"><ng-content /></div>
            </div>
            <div class="languages">
              <p class="rule">Change Language</p>
              <div class="flags" role="group" aria-label="Change language">
                @for (lang of languages; track lang.id) {
                  <button type="button" class="flag" [class.active]="language() === lang.id"
                          [attr.aria-pressed]="language() === lang.id" [attr.aria-label]="lang.label"
                          (click)="language.set(lang.id)">
                    <img [src]="lang.flag" alt="" width="32" height="24" />
                  </button>
                }
              </div>
            </div>
            <footer class="disclaimer">
              <p>Please ensure you are playing from a jurisdiction where online gaming is legal and regulated.</p>
              <p>Gaming is strictly for individuals aged 21 and above. Play responsibly and stay in control.</p>
            </footer>
          </div>
        }
      </div>
    </main>
  `,
})
export class LoginLayoutComponent implements OnInit {
  readonly appName = input('SWC MAIN');
  /** Tenant logo; the SWC logo is used when empty. */
  readonly logoUrl = input<string | null>(null);
  /** The line under the name (Administration, Platform Control); "Online Gaming Portal" when empty. */
  readonly portalName = input<string | null>(null);
  /** Play the logo reveal before showing the form, once per browser session (public website). */
  readonly splash = input(false);
  readonly usernameLabel = input('Username');
  readonly usernameType = input<'text' | 'email'>('text');
  readonly username = input.required<FormControl<string>>();
  readonly password = input.required<FormControl<string>>();
  readonly busy = input(false);
  readonly disabled = input(false);
  readonly error = input<string | null>(null);
  readonly submitted = output<void>();

  private readonly reveal = inject(BrandRevealService);
  private readonly destroyRef = inject(DestroyRef);
  readonly ids = { user: `login-user-${++nextId}`, userError: `login-user-error-${nextId}`, pass: `login-pass-${nextId}`, passError: `login-pass-error-${nextId}` };
  readonly showPassword = signal(false);
  readonly splashing = signal(false);
  /** Highlights a flag. Does not translate the page. */
  readonly language = signal<(typeof LANGUAGES)[number]['id']>('en');
  readonly languages = LANGUAGES;
  intro = false;

  ngOnInit(): void {
    this.intro = this.splash() && !this.logoUrl() && this.reveal.claim();
    if (!this.intro || globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    this.splashing.set(true);
    const timer = setTimeout(() => this.splashing.set(false), BRAND_REVEAL_MS);
    this.destroyRef.onDestroy(() => clearTimeout(timer));
  }

  invalid(control: FormControl<string>): boolean {
    return control.invalid && control.touched;
  }

  onSubmit(event: Event): void {
    event.preventDefault();
    if (!this.busy() && !this.disabled()) this.submitted.emit();
  }
}

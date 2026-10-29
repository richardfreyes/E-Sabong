import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, input, output, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';

import { BRAND_REVEAL_MS, BrandLogoComponent, BrandRevealService } from './brand-logo';
import { FieldErrorComponent } from './primitives';

let nextId = 0;

/**
 * Full-page centred sign-in shared by every portal. It renders the form only; the page that
 * hosts it owns the controls and the sign-in call. Optional extras per portal: a portal name
 * under the logo, and a one-time logo reveal before the form appears.
 * Project `[loginNotice]` content above the fields and anything else (links) below the form.
 */
@Component({
  selector: 'app-login-layout',
  imports: [ReactiveFormsModule, MatIconModule, BrandLogoComponent, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './login-layout.scss',
  template: `
    <main class="login">
      <div class="panel">
        <header class="masthead">
          <span class="glow red" aria-hidden="true"></span><span class="glow blue" aria-hidden="true"></span>
          <app-brand-logo class="hero" [variant]="intro ? 'reveal' : 'static'" [once]="false" [size]="280"
                          [src]="logoUrl()" [alt]="logoUrl() ? appName() : 'E-Sabong'" />
          @if (portalName()) { <h1 class="portal">{{ portalName() }}</h1> }
          @else { <h1 class="sr-only">Sign in to {{ appName() }}</h1> }
        </header>
        @if (splashing()) {
          <button type="button" class="skip" (click)="splashing.set(false)">Skip intro</button>
        } @else {
          <div class="body">
            <ng-content select="[loginNotice]" />
            <form class="form" (submit)="onSubmit($event)" novalidate>
              <div class="field">
                <label [for]="ids.user">{{ usernameLabel() }}</label>
                <input [id]="ids.user" class="input" [type]="usernameType()" [formControl]="username()" autocomplete="username"
                       autocapitalize="none" spellcheck="false" [attr.aria-invalid]="invalid(username())"
                       [attr.aria-describedby]="invalid(username()) ? ids.userError : null" />
                @if (invalid(username())) { <p class="field-error" [id]="ids.userError"><app-field-error [control]="username()" /></p> }
              </div>
              <div class="field">
                <label [for]="ids.pass">Password</label>
                <div class="control">
                  <input [id]="ids.pass" class="input has-toggle" [type]="showPassword() ? 'text' : 'password'" [formControl]="password()"
                         autocomplete="current-password" [attr.aria-invalid]="invalid(password())"
                         [attr.aria-describedby]="invalid(password()) ? ids.passError : null" />
                  <button type="button" class="toggle" aria-label="Show password" [attr.aria-pressed]="showPassword()" [attr.aria-controls]="ids.pass"
                          (click)="showPassword.set(!showPassword())">
                    <mat-icon aria-hidden="true">{{ showPassword() ? 'visibility_off' : 'visibility' }}</mat-icon>
                  </button>
                </div>
                @if (invalid(password())) { <p class="field-error" [id]="ids.passError"><app-field-error [control]="password()" /></p> }
              </div>
              @if (error()) { <p class="form-error" role="alert">{{ error() }}</p> }
              <button type="submit" class="submit" [disabled]="busy() || disabled()" [attr.aria-busy]="busy()">
                @if (busy()) { <span class="spinner" aria-hidden="true"></span>Signing in… } @else { Sign in }
              </button>
            </form>
            <ng-content />
          </div>
        }
      </div>
    </main>
  `,
})
export class LoginLayoutComponent implements OnInit {
  readonly appName = input('E-Sabong');
  /** Tenant logo; the E-Sabong logo is used when empty. */
  readonly logoUrl = input<string | null>(null);
  /** Shown under the logo (Admin, Super Admin). */
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

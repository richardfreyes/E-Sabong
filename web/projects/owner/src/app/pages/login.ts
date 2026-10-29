import { ChangeDetectionStrategy, Component, inject, input, signal } from '@angular/core';
import { FormBuilder, Validators } from '@angular/forms';
import { Router } from '@angular/router';

import { LoginLayoutComponent } from '@shared/components';
import { ApiError, AuthStore } from '@shared/services';

@Component({
  selector: 'app-owner-login',
  imports: [LoginLayoutComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-login-layout appName="Platform Control" portalName="Platform Control" usernameLabel="Email" usernameType="email"
                      [username]="form.controls.email" [password]="form.controls.password"
                      [busy]="busy()" [error]="error()" (submitted)="submit()">
      @if (reason() === 'expired') {
        <p loginNotice class="login-notice" role="status">Your session ended. Sign in again.</p>
      }
    </app-login-layout>
  `,
})
export class LoginPage {
  readonly returnUrl = input<string>('/');
  readonly reason = input<string | null>(null);
  private readonly auth = inject(AuthStore);
  private readonly router = inject(Router);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', Validators.required],
  });

  async submit(): Promise<void> {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.auth.login(this.form.getRawValue());
      await this.router.navigateByUrl(this.returnUrl() || '/');
    } catch (err) {
      this.error.set((err as ApiError).message);
    } finally {
      this.busy.set(false);
    }
  }
}

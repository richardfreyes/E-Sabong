import { ChangeDetectionStrategy, Component, inject, input, output, signal } from '@angular/core';
import { AbstractControl, FormBuilder, ReactiveFormsModule, ValidationErrors, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { Observable } from 'rxjs';

import { toApiError } from '../services/api-client';
import { FieldErrorComponent } from './primitives';

/** Mirrors validate_password_policy in api/app/core/security.py: length only, any characters. */
export const PASSWORD_MIN_LENGTH = 6;
export const PASSWORD_MAX_LENGTH = 128;
export const PASSWORD_TOO_SHORT = `Password must be at least ${PASSWORD_MIN_LENGTH} characters.`;

export function passwordPolicy(control: AbstractControl): ValidationErrors | null {
  const v = String(control.value ?? '');
  if (!v) return null; // pair with Validators.required where the field is mandatory
  if (v.length < PASSWORD_MIN_LENGTH) return { server: PASSWORD_TOO_SHORT };
  if (v.length > PASSWORD_MAX_LENGTH) return { server: `Password must be at most ${PASSWORD_MAX_LENGTH} characters.` };
  return null;
}

function matching(group: AbstractControl): ValidationErrors | null {
  return group.get('new_password')?.value === group.get('confirm')?.value ? null : { mismatch: true };
}

/** Change-password form; the parent supplies the API call. */
@Component({
  selector: 'app-password-form',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form [formGroup]="form" (ngSubmit)="submit()" class="flex flex-col gap-1" novalidate>
      <mat-form-field appearance="outline">
        <mat-label>Current password</mat-label>
        <input matInput type="password" formControlName="current_password" autocomplete="current-password" />
        <mat-error><app-field-error [control]="form.controls.current_password" /></mat-error>
      </mat-form-field>
      <mat-form-field appearance="outline">
        <mat-label>New password</mat-label>
        <input matInput type="password" formControlName="new_password" autocomplete="new-password" />
        <mat-hint>At least {{ minLength }} characters</mat-hint>
        <mat-error><app-field-error [control]="form.controls.new_password" /></mat-error>
      </mat-form-field>
      <mat-form-field appearance="outline">
        <mat-label>Confirm new password</mat-label>
        <input matInput type="password" formControlName="confirm" autocomplete="new-password" />
        @if (form.hasError('mismatch') && form.controls.confirm.touched) {
          <mat-hint class="!text-error">Passwords do not match</mat-hint>
        }
      </mat-form-field>
      @if (error()) {
        <p class="m-0 mb-2 text-sm text-error" role="alert">{{ error() }}</p>
      }
      <button mat-flat-button type="submit" [disabled]="busy()" [attr.aria-busy]="busy()">{{ busy() ? 'Saving…' : submitLabel() }}</button>
    </form>
  `,
})
export class PasswordFormComponent {
  readonly submitLabel = input('Update password');
  readonly save = input.required<(current: string, next: string) => Observable<unknown>>();
  readonly changed = output<void>();

  readonly minLength = PASSWORD_MIN_LENGTH;
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).nonNullable.group(
    {
      current_password: ['', Validators.required],
      new_password: ['', [Validators.required, passwordPolicy]],
      confirm: ['', Validators.required],
    },
    { validators: matching },
  );

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const { current_password, new_password } = this.form.getRawValue();
    this.busy.set(true);
    this.error.set(null);
    this.save()(current_password, new_password).subscribe({
      next: () => {
        this.busy.set(false);
        this.form.reset();
        this.changed.emit();
      },
      error: (err: unknown) => {
        this.busy.set(false);
        this.error.set(toApiError(err).message);
      },
    });
  }
}

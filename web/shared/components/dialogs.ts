import { ChangeDetectionStrategy, Component, Injectable, inject } from '@angular/core';
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { firstValueFrom } from 'rxjs';

import { FieldErrorComponent } from './primitives';

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmText?: string;
  /** Label for the dismiss button; defaults to "Cancel". */
  cancelText?: string;
  destructive?: boolean;
  /** Ask for free text (reason, reference…); the dialog returns it. */
  inputLabel?: string;
  inputRequired?: boolean;
  inputMinLength?: number;
}

@Component({
  selector: 'app-confirm-dialog',
  imports: [MatDialogModule, MatButtonModule, MatFormFieldModule, MatInputModule, ReactiveFormsModule, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ data.title }}</h2>
    <mat-dialog-content>
      <p class="m-0 mb-4 text-on-surface-variant">{{ data.message }}</p>
      @if (data.inputLabel) {
        <mat-form-field appearance="outline" class="w-full">
          <mat-label>{{ data.inputLabel }}</mat-label>
          <textarea matInput [formControl]="input" rows="2" cdkFocusInitial></textarea>
          <mat-error><app-field-error [control]="input" /></mat-error>
        </mat-form-field>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close>{{ data.cancelText ?? 'Cancel' }}</button>
      <button mat-flat-button type="button" [class.destructive-action]="data.destructive" (click)="confirm()">
        {{ data.confirmText ?? 'Confirm' }}
      </button>
    </mat-dialog-actions>
  `,
})
export class ConfirmDialogComponent {
  readonly data = inject<ConfirmOptions>(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<ConfirmDialogComponent, { value: string } | undefined>);
  readonly input = new FormControl('', {
    nonNullable: true,
    validators: [
      ...(this.data.inputRequired ? [Validators.required] : []),
      ...(this.data.inputMinLength ? [Validators.minLength(this.data.inputMinLength)] : []),
      Validators.maxLength(300),
    ],
  });

  confirm(): void {
    if (this.input.invalid) {
      this.input.markAsTouched();
      return;
    }
    this.ref.close({ value: this.input.value.trim() });
  }
}

@Injectable({ providedIn: 'root' })
export class ConfirmService {
  private readonly dialog = inject(MatDialog);

  /** Resolves to the entered text ('' when no input) or null when cancelled. */
  async ask(options: ConfirmOptions): Promise<string | null> {
    const ref = this.dialog.open<ConfirmDialogComponent, ConfirmOptions, { value: string }>(ConfirmDialogComponent, {
      data: options,
      width: '440px',
      maxWidth: '95vw',
      autoFocus: options.inputLabel ? 'first-tabbable' : 'dialog',
    });
    const result = await firstValueFrom(ref.afterClosed());
    return result ? result.value : null;
  }
}

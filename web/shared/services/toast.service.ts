import { Injectable, inject } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';

import { ApiError, toApiError } from './api-client';

@Injectable({ providedIn: 'root' })
export class ToastService {
  private readonly snack = inject(MatSnackBar);

  success(message: string): void {
    this.snack.open(message, 'Dismiss', { duration: 4000, panelClass: 'toast-success', politeness: 'polite' });
  }

  info(message: string): void {
    this.snack.open(message, 'Dismiss', { duration: 4000, politeness: 'polite' });
  }

  error(errOrMessage: unknown): void {
    const message = typeof errOrMessage === 'string' ? errOrMessage : describeError(toApiError(errOrMessage));
    this.snack.open(message, 'Dismiss', { duration: 7000, panelClass: 'toast-error', politeness: 'assertive' });
  }
}

export function describeError(err: ApiError): string {
  if (err.code === 'validation_error') {
    const fields = Object.entries(err.fieldErrors);
    if (fields.length) return fields.map(([f, m]) => `${f.replace(/_/g, ' ')}: ${m}`).join('; ');
  }
  return err.message;
}

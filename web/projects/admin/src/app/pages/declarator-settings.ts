import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { finalize } from 'rxjs';
import { ToastService } from '@shared/services';
import { QuickBetSettingsApi, defaultQuickBets } from '@shared/services';

@Component({
  selector: 'app-declarator-settings',
  imports: [ReactiveFormsModule, MatButtonModule, MatFormFieldModule, MatInputModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form [formGroup]="form" (ngSubmit)="save()" class="max-w-3xl py-4" novalidate>
      <h2 class="mt-0 text-lg font-semibold">Quick Bet Amount Configuration</h2>
      <p class="text-sm text-on-surface-variant">Set four ghost-bet amounts for each side. Saved changes apply to this workspace's Declarators immediately.</p>
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        @for (side of sides; track side) {
          <fieldset class="m-0 min-w-0 border-0 p-0" [formArrayName]="side">
            <legend class="mb-3 font-semibold">{{ side.toUpperCase() }}</legend>
            <div class="grid grid-cols-2 gap-x-3">
              @for (control of form.controls[side].controls; track $index; let i = $index) {
                <mat-form-field>
                  <mat-label>Button {{ i + 1 }}</mat-label>
                  <input matInput inputmode="decimal" [formControlName]="i" [attr.aria-label]="side.toUpperCase() + ' button ' + (i + 1)" />
                  <mat-error>Positive amount, up to two decimals</mat-error>
                </mat-form-field>
              }
            </div>
          </fieldset>
        }
      </div>
      <div class="flex flex-wrap gap-2">
        <button mat-flat-button type="submit" [disabled]="loading() || loadError() || saving() || form.invalid">Save Changes</button>
        <button mat-button type="button" (click)="reset()" [disabled]="loading() || loadError() || saving()">Reset to Default</button>
      </div>
      @if (loadError()) { <p role="alert" class="text-error">Could not load Declarator settings. <button mat-button type="button" (click)="load()">Retry</button></p> }
    </form>
  `,
})
export class DeclaratorSettingsComponent {
  private readonly api = inject(QuickBetSettingsApi);
  private readonly toast = inject(ToastService);
  private readonly fb = inject(FormBuilder).nonNullable;
  readonly sides = ['meron', 'wala'] as const;
  readonly loading = signal(true);
  readonly saving = signal(false);
  readonly loadError = signal(false);
  private amounts() {
    return this.fb.array(defaultQuickBets().meron.map(v => this.fb.control(v, [Validators.required,
      Validators.pattern(/^\d+(\.\d{1,2})?$/), Validators.min(0.01), Validators.max(999999999999.99)])));
  }
  readonly form = this.fb.group({ meron: this.amounts(), wala: this.amounts() });
  constructor() { this.load(); }
  load(): void {
    this.loading.set(true); this.loadError.set(false);
    this.api.get().pipe(finalize(() => this.loading.set(false))).subscribe({
      next: config => this.form.setValue(config),
      error: error => { this.loadError.set(true); this.toast.error(error); },
    });
  }
  reset(): void { this.form.setValue(defaultQuickBets()); this.save(); }
  save(): void {
    if (this.loading() || this.saving() || this.loadError() || this.form.invalid) { this.form.markAllAsTouched(); return; }
    this.saving.set(true);
    this.api.save(this.form.getRawValue()).pipe(finalize(() => this.saving.set(false))).subscribe({
      next: config => { this.form.setValue(config); this.form.markAsPristine(); this.toast.success('Declarator settings saved'); },
      error: error => this.toast.error(error),
    });
  }
}

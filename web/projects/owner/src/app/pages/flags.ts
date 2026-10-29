import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';

import { CellDefDirective, Column, DataTableComponent, FieldErrorComponent, PageHeaderComponent } from '@shared/components';
import type { FeatureFlag } from '@shared/models';
import { PlatformApi, ToastService, toApiError } from '@shared/services';

@Component({
  selector: 'app-owner-flags',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule,
            MatSlideToggleModule, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Feature flags" subtitle="Platform-wide defaults; override per tenant from the tenant page." />
    <form [formGroup]="form" (ngSubmit)="create()" class="mb-4 flex flex-col gap-2 rounded-2xl border border-outline-variant p-4 md:flex-row md:items-start">
      <mat-form-field class="md:w-64" subscriptSizing="dynamic"><mat-label>Key</mat-label><input matInput formControlName="key" placeholder="new_feature" />
        <mat-error><app-field-error [control]="form.controls.key" /></mat-error></mat-form-field>
      <mat-form-field class="flex-1" subscriptSizing="dynamic"><mat-label>Description</mat-label><input matInput formControlName="description" /></mat-form-field>
      <mat-slide-toggle formControlName="default_enabled" class="md:mt-4">On by default</mat-slide-toggle>
      <button mat-flat-button type="submit" class="md:mt-2">Add flag</button>
    </form>
    <app-data-table [columns]="columns" [rows]="flags()" [total]="flags().length" [loading]="loading()" [error]="error()" [searchable]="false"
                    [paginated]="false" emptyTitle="No flags" emptyIcon="toggle_on" (retry)="load()">
      <ng-template appCell="key" let-row><code>{{ row.key }}</code></ng-template>
      <ng-template appCell="default_enabled" let-row>
        <mat-slide-toggle [checked]="row.default_enabled" (change)="toggle(row, $event.checked)" [attr.aria-label]="'Default for ' + row.key" />
      </ng-template>
    </app-data-table>
  `,
})
export class FlagsPage {
  private readonly api = inject(PlatformApi);
  private readonly toast = inject(ToastService);
  readonly flags = signal<FeatureFlag[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly columns: Column<FeatureFlag>[] = [
    { key: 'key', label: 'Key' },
    { key: 'description', label: 'Description', hideOnMobile: true },
    { key: 'default_enabled', label: 'Default', align: 'end' },
  ];
  readonly form = inject(FormBuilder).nonNullable.group({
    key: ['', [Validators.required, Validators.pattern(/^[a-z0-9_.-]{2,64}$/)]],
    description: [''],
    default_enabled: [false],
  });

  constructor() {
    this.load();
  }

  load(): void {
    this.api.flags().subscribe({
      next: (f) => (this.flags.set(f), this.loading.set(false)),
      error: (e) => (this.error.set(toApiError(e).message), this.loading.set(false)),
    });
  }

  create(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.api.createFlag(this.form.getRawValue()).subscribe({
      next: () => (this.form.reset(), this.toast.success('Flag created'), this.load()),
      error: (e) => this.toast.error(e),
    });
  }

  toggle(flag: FeatureFlag, value: boolean): void {
    this.api.updateFlag(flag.id, { default_enabled: value }).subscribe({ next: () => this.load(), error: (e) => this.toast.error(e) });
  }
}

import { ChangeDetectionStrategy, Component, DestroyRef, forwardRef, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ControlValueAccessor, FormControl, NG_VALUE_ACCESSOR, ReactiveFormsModule } from '@angular/forms';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { debounceTime, distinctUntilChanged, filter, switchMap } from 'rxjs';

import type { User } from '@shared/models';
import { LabelPipe, PointsPipe } from '@shared/pipes';
import { AdminApi } from '@shared/services';

/** Searchable account selector; its value is the selected user id. */
@Component({
  selector: 'app-user-picker',
  imports: [ReactiveFormsModule, MatAutocompleteModule, MatFormFieldModule, MatInputModule, LabelPipe, PointsPipe],
  providers: [{ provide: NG_VALUE_ACCESSOR, useExisting: forwardRef(() => UserPickerComponent), multi: true }],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <mat-form-field class="w-full">
      <mat-label>{{ label() }}</mat-label>
      <input matInput [formControl]="search" [matAutocomplete]="auto" placeholder="Type a username or name" (blur)="onTouched()" />
      <mat-autocomplete #auto="matAutocomplete" [displayWith]="display" (optionSelected)="select($event.option.value)">
        @for (u of options(); track u.id) {
          <mat-option [value]="u">
            <span class="font-medium">{{ u.username }}</span>
            <span class="ml-2 text-xs text-on-surface-variant">{{ u.full_name }} · {{ u.account_type | label }} · {{ u.balance | points }} pts</span>
          </mat-option>
        }
      </mat-autocomplete>
      @if (hint()) { <mat-hint>{{ hint() }}</mat-hint> }
    </mat-form-field>
  `,
})
export class UserPickerComponent implements ControlValueAccessor {
  readonly label = input('Account');
  readonly hint = input<string | null>(null);
  readonly excludeId = input<string | null>(null);
  private readonly api = inject(AdminApi);
  readonly search = new FormControl<string | User>('', { nonNullable: true });
  readonly options = signal<User[]>([]);
  private onChange: (v: string | null) => void = () => undefined;
  onTouched: () => void = () => undefined;

  constructor() {
    this.search.valueChanges
      .pipe(
        filter((v): v is string => typeof v === 'string'),
        debounceTime(250),
        distinctUntilChanged(),
        switchMap((q) => this.api.users({ q, page_size: 10, status: 'active' })),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((p) => this.options.set(p.items.filter((u) => u.id !== this.excludeId())));
  }

  display = (u: User | string | null): string => (typeof u === 'string' ? u : u ? `${u.username} — ${u.full_name}` : '');

  select(u: User): void {
    this.onChange(u.id);
  }

  writeValue(id: string | null): void {
    if (!id) {
      this.search.setValue('', { emitEvent: false });
      return;
    }
    this.api.user(id).subscribe({ next: (u) => this.search.setValue(u, { emitEvent: false }), error: () => undefined });
  }

  registerOnChange(fn: (v: string | null) => void): void {
    this.onChange = fn;
  }

  registerOnTouched(fn: () => void): void {
    this.onTouched = fn;
  }

  setDisabledState(disabled: boolean): void {
    if (disabled) this.search.disable();
    else this.search.enable();
  }
}

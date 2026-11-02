import { ChangeDetectionStrategy, Component, DestroyRef, computed, forwardRef, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { ControlValueAccessor, FormControl, NG_VALUE_ACCESSOR, ReactiveFormsModule } from '@angular/forms';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { Subscription, catchError, combineLatest, debounceTime, distinctUntilChanged, filter, of, startWith, switchMap } from 'rxjs';

import type { AccountType, User } from '@shared/models';
import { LabelPipe, PointsPipe } from '@shared/pipes';
import { AdminApi, RealtimeService } from '@shared/services';

/** Uses the existing scoped account API, including its transfer eligibility filter. */
@Component({
  selector: 'app-user-picker',
  imports: [ReactiveFormsModule, MatAutocompleteModule, MatFormFieldModule, MatInputModule, LabelPipe, PointsPipe],
  providers: [{ provide: NG_VALUE_ACCESSOR, useExisting: forwardRef(() => UserPickerComponent), multi: true }],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `.identity { display:block; overflow-wrap:anywhere; } .detail { display:block; font-size:.75rem; color:var(--text-secondary); overflow-wrap:anywhere; }`,
  template: `
    <mat-form-field class="w-full" subscriptSizing="dynamic">
      <mat-label>{{ label() }}</mat-label>
      <input matInput [formControl]="search" [matAutocomplete]="auto" placeholder="Name, username, role or account ID" (blur)="onTouched()" />
      <mat-autocomplete #auto="matAutocomplete" [displayWith]="display" (optionSelected)="select($event.option.value)">
        @for (u of options(); track u.id) {
          <mat-option [value]="u">
            <span class="identity">{{ u.username }} · {{ u.full_name }}</span>
            <span class="detail">{{ u.account_type | label }} · {{ u.balance | points }} points</span>
          </mat-option>
        }
      </mat-autocomplete>
      <mat-hint>{{ failed() ? 'Accounts could not be loaded. Type to retry.' : hint() ?? 'Select a matching account from the results.' }}</mat-hint>
    </mat-form-field>
  `,
})
export class UserPickerComponent implements ControlValueAccessor {
  readonly label = input('Account');
  readonly hint = input<string | null>(null);
  readonly excludeId = input<string | null>(null);
  readonly transferOnly = input(false);
  readonly accountType = input<AccountType | null>(null);
  readonly selection = output<User | null>();
  private readonly api = inject(AdminApi);
  private readonly destroy = inject(DestroyRef);
  private request?: Subscription;
  readonly search = new FormControl<string | User>('', { nonNullable: true });
  readonly options = signal<User[]>([]);
  readonly selected = signal<User | null>(null);
  readonly failed = signal(false);
  private onChange: (v: string | null) => void = () => undefined;
  onTouched: () => void = () => undefined;

  constructor() {
    this.search.valueChanges.pipe(takeUntilDestroyed(this.destroy)).subscribe(value => {
      if (typeof value === 'string') {
        this.request?.unsubscribe();
        this.selected.set(null);
        this.selection.emit(null);
        this.onChange(null); // Edited text is not a selected recipient.
      }
    });
    const config = computed(() => ({ account_type: this.accountType(), transfer_recipients: this.transferOnly(), exclude: this.excludeId() }));
    combineLatest([
      this.search.valueChanges.pipe(filter((v): v is string => typeof v === 'string'), startWith(''), debounceTime(250), distinctUntilChanged()),
      toObservable(config),
    ]).pipe(
      switchMap(([q, filters]) => this.api.users({ q, page_size: 20, status: 'active', account_type: filters.account_type, transfer_recipients: filters.transfer_recipients }).pipe(
        catchError(() => { this.failed.set(true); return of(null); }),
      )),
      takeUntilDestroyed(this.destroy),
    ).subscribe(page => {
      if (!page) { this.options.set([]); return; }
      this.failed.set(false);
      this.options.set(page.items.filter(user => user.id !== this.excludeId()));
    });
    inject(RealtimeService).on('points.updated', 'connected').pipe(debounceTime(100), takeUntilDestroyed(this.destroy)).subscribe(() => {
      const id = this.selected()?.id;
      if (id) this.read(id);
    });
  }

  display = (user: User | string | null): string => typeof user === 'string' ? user : user ? `${user.username} — ${user.full_name}` : '';

  select(user: User): void {
    this.request?.unsubscribe();
    this.selected.set(user);
    this.selection.emit(user);
    this.onChange(user.id);
    this.onTouched();
    this.read(user.id);
  }

  private read(id: string): void {
    this.request?.unsubscribe();
    this.request = this.api.user(id).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: user => { this.selected.set(user); this.selection.emit(user); this.search.setValue(user, { emitEvent: false }); },
      error: () => { this.failed.set(true); },
    });
  }

  writeValue(id: string | null): void {
    this.request?.unsubscribe();
    this.selected.set(null);
    this.selection.emit(null);
    if (!id) this.search.setValue('');
    else this.read(id);
  }
  registerOnChange(fn: (v: string | null) => void): void { this.onChange = fn; }
  registerOnTouched(fn: () => void): void { this.onTouched = fn; }
  setDisabledState(disabled: boolean): void {
    if (disabled) this.search.disable({ emitEvent: false });
    else this.search.enable({ emitEvent: false });
  }
}

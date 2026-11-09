import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { CommissionApi, MasterAllocation, ToastService, toApiError } from '@shared/services';

/** "0.0850" -> "8.5" (no trailing zeros). */
export function toPercent(fraction: string): string {
  return String(Math.round(Number(fraction) * 10000) / 100);
}

/** "8.5" -> "0.0850". The input allows at most two decimals, so four fraction places are exact. */
export function toFraction(percent: string): string {
  return (Math.round(Number(percent) * 100) / 10000).toFixed(4);
}

/**
 * Administrators: a master agent's commission rate (default 8.5%, 0% to 100%). The server keeps
 * one rate per master agent; bets placed after a change use the new rate, earlier ones keep theirs.
 */
@Component({
  selector: 'app-master-commission',
  imports: [ReactiveFormsModule, MatButtonModule, MatFormFieldModule, MatInputModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="rounded-2xl border border-outline-variant p-4" aria-labelledby="commission-title" data-testid="master-commission">
      <h2 id="commission-title" class="m-0 mb-3 text-base font-semibold">Commission</h2>
      @if (error()) {
        <p class="m-0 text-sm text-error" role="alert">{{ error() }}</p>
        <button mat-button type="button" class="mt-2" (click)="load()">Retry</button>
      } @else if (rate(); as r) {
        <dl class="m-0 mb-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
          <dt class="text-on-surface-variant">Username</dt><dd class="m-0">{{ r.username }}</dd>
          <dt class="text-on-surface-variant">Account type</dt><dd class="m-0">Master Agent</dd>
          <dt class="text-on-surface-variant">Current commission rate</dt>
          <dd class="m-0 font-semibold" data-field="current-rate">{{ percent(r.allocation) }}%@if (r.is_default) {<span class="ml-1 font-normal text-on-surface-variant">(default)</span>}</dd>
        </dl>
        <form (submit)="$event.preventDefault(); save()" class="flex flex-col gap-2 sm:flex-row sm:items-start sm:gap-3" novalidate>
          <mat-form-field class="w-full sm:max-w-56">
            <mat-label>Commission Rate (%)</mat-label>
            <input matInput type="number" inputmode="decimal" min="0" max="100" step="0.01" [formControl]="control" data-field="commission-input" />
            <span matTextSuffix>%</span>
            @if (control.invalid) { <mat-error>Enter a percentage from 0 to 100 with up to two decimals.</mat-error> }
          </mat-form-field>
          <button mat-flat-button type="submit" class="sm:mt-2" [disabled]="busy() || control.invalid || !changed()" data-action="save-commission">Save Changes</button>
        </form>
        @if (minimum() > 0) {
          <p class="m-0 text-xs text-on-surface-variant">Cannot be lower than {{ minimum() }}%, the highest rate this master agent gave an agent.</p>
        }
        <p class="m-0 mt-1 text-xs text-on-surface-variant">Applies to bets placed after saving. Commission already earned keeps its rate.</p>
      } @else {
        <p class="m-0 text-sm text-on-surface-variant">Loading…</p>
      }
    </section>
  `,
})
export class MasterCommissionCard {
  readonly masterId = input.required<string>();
  private readonly api = inject(CommissionApi);
  private readonly toast = inject(ToastService);

  readonly rate = signal<MasterAllocation | null>(null);
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);
  readonly control = new FormControl('', {
    nonNullable: true,
    validators: [Validators.required, Validators.min(0), Validators.max(100), Validators.pattern(/^\d{1,3}(\.\d{1,2})?$/)],
  });
  private readonly value = signal('');
  readonly changed = computed(() => {
    const r = this.rate();
    return !!r && this.value() !== '' && toFraction(this.value()) !== Number(r.allocation).toFixed(4);
  });
  readonly minimum = computed(() => Number(toPercent(this.rate()?.highest_agent_rate ?? '0')));
  readonly percent = toPercent;

  constructor() {
    this.control.valueChanges.subscribe((v) => this.value.set(String(v ?? '')));
    effect(() => {
      this.masterId();
      this.load();
    });
  }

  load(): void {
    this.error.set(null);
    this.api.masterAllocation(this.masterId()).subscribe({
      next: (r) => this.show(r),
      error: (e: unknown) => this.error.set(toApiError(e).message),
    });
  }

  save(): void {
    if (this.control.invalid || this.busy()) {
      this.control.markAsTouched();
      return;
    }
    this.busy.set(true);
    this.api.setMasterAllocation(this.masterId(), toFraction(String(this.control.value))).subscribe({
      next: (r) => {
        this.busy.set(false);
        this.show({ ...r, is_default: false });
        this.toast.success(`Commission for ${r.username} set to ${toPercent(r.allocation)}%.`);
      },
      error: (e: unknown) => {
        this.busy.set(false);
        this.toast.error(e);
      },
    });
  }

  private show(r: MasterAllocation): void {
    this.rate.set(r);
    this.control.setValue(toPercent(r.allocation));
    this.control.markAsPristine();
  }
}

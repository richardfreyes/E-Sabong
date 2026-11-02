import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators, type AbstractControl, type ValidationErrors } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';

import type { BettingSettings } from '@shared/models';
import { CreditsPipe } from '@shared/pipes';
import { AdminApi, ToastService } from '@shared/services';

const MONEY = Validators.pattern(/^\d+(\.\d{1,2})?$/);

function limitsInOrder(group: AbstractControl): ValidationErrors | null {
  const { min_bet, max_bet, max_fight_stake } = group.value as Record<string, string>;
  if (Number(max_bet) < Number(min_bet)) return { limits: 'The maximum bet must be at least the minimum bet.' };
  if (Number(max_fight_stake) < Number(max_bet)) return { limits: 'The per-fight limit must be at least the maximum bet.' };
  return null;
}

/**
 * Simulated betting configuration. MERON and WALA odds are never set here: they come from the
 * live pools, less the pool deduction. Changes apply to new bets and newly opened fights only.
 */
@Component({
  selector: 'app-betting-settings',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule, MatSlideToggleModule, CreditsPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form [formGroup]="form" (ngSubmit)="save()" class="flex max-w-3xl flex-col gap-3 py-4" novalidate>
      <p class="m-0 text-sm text-on-surface-variant">
        Players bet <strong>play credits</strong> granted by the system. It has no cash value, cannot be bought, transferred,
        adjusted or redeemed, and is separate from account points.
      </p>
      <mat-slide-toggle formControlName="enabled">Enable betting in the player portal</mat-slide-toggle>
      <p class="m-0 text-xs text-on-surface-variant">
        Players see the betting board as soon as this is saved. Bets are accepted only on fights where an operator has opened betting:
        <strong>Events → event → fight card → Open betting</strong>. Closing it locks the final odds.
      </p>

      <fieldset class="m-0 mt-2 border-0 p-0">
        <legend class="mb-1 text-sm font-semibold">Odds and returns</legend>
        <p class="m-0 mb-3 text-xs text-on-surface-variant">
          MERON and WALA are pool-based: odds = (MERON + WALA pool) × (1 − deduction) ÷ side pool, recalculated with every bet and locked
          when betting closes. DRAW returns a fixed multiplier locked on each bet; when DRAW wins, MERON and WALA stakes are refunded.
        </p>
        <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
          <mat-form-field>
            <mat-label>Pool deduction</mat-label>
            <input matInput formControlName="commission_percent" inputmode="decimal" />
            <span matTextSuffix>%</span>
            <mat-hint>Example: 10 → equal pools pay {{ examplePayout() }}</mat-hint>
            <mat-error>0 to 49.99, up to two decimals</mat-error>
          </mat-form-field>
          <mat-form-field>
            <mat-label>DRAW multiplier</mat-label>
            <span matTextPrefix>×&nbsp;</span>
            <input matInput formControlName="draw_multiplier" inputmode="decimal" />
            <mat-hint>Gross return per unit staked, stake included</mat-hint>
            <mat-error>1 to 100, up to two decimals</mat-error>
          </mat-form-field>
        </div>
      </fieldset>

      <fieldset class="m-0 mt-2 border-0 p-0">
        <legend class="mb-3 text-sm font-semibold">Limits</legend>
        <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-3">
          @for (f of moneyFields; track f.key) {
            <mat-form-field>
              <mat-label>{{ f.label }}</mat-label>
              <span matTextSuffix>&nbsp;credits</span>
              <input matInput [formControlName]="f.key" inputmode="decimal" />
              <mat-hint>{{ f.hint }}</mat-hint>
              <mat-error>Enter an amount with up to two decimals</mat-error>
            </mat-form-field>
          }
        </div>
        @if (current()?.enabled && current()?.starting_balance === '0.00') {
          <p class="m-0 text-sm text-error" role="alert">No starting play credits are configured: players have nothing to bet with until you set them.</p>
        }
        @if (form.errors?.['limits']; as message) { <p class="m-0 text-sm text-error" role="alert">{{ message }}</p> }
      </fieldset>

      @if (current(); as c) {
        <p class="m-0 text-xs text-on-surface-variant">
          Current: deduction {{ percent(c.commission_rate) }}% · DRAW ×{{ c.draw_multiplier }} · bets {{ c.min_bet | credits }}–{{ c.max_bet | credits }}
          · {{ c.max_fight_stake | credits }} per fight · starting balance {{ c.starting_balance | credits }}
        </p>
      }
      <div class="flex gap-2"><button mat-flat-button type="submit" [disabled]="saving()">Save betting settings</button></div>
    </form>
  `,
})
export class BettingSettingsComponent {
  private readonly api = inject(AdminApi);
  private readonly toast = inject(ToastService);
  private readonly fb = inject(FormBuilder);

  readonly current = signal<BettingSettings | null>(null);
  readonly saving = signal(false);
  readonly moneyFields = [
    { key: 'min_bet', label: 'Minimum bet', hint: 'Per bet' },
    { key: 'max_bet', label: 'Maximum bet', hint: 'Per bet' },
    { key: 'max_fight_stake', label: 'Per-fight limit', hint: 'Total per player per fight' },
    { key: 'starting_balance', label: 'Starting play credits', hint: 'Granted once per player, recorded in the ledger' },
  ] as const;

  readonly form = this.fb.nonNullable.group(
    {
      enabled: [false],
      commission_percent: ['10', [Validators.required, Validators.pattern(/^\d{1,2}(\.\d{1,2})?$/), Validators.max(49.99)]],
      draw_multiplier: ['8', [Validators.required, Validators.pattern(/^\d{1,3}(\.\d{1,2})?$/), Validators.min(1), Validators.max(100)]],
      min_bet: ['10', [Validators.required, MONEY]],
      max_bet: ['10000', [Validators.required, MONEY]],
      max_fight_stake: ['50000', [Validators.required, MONEY]],
      starting_balance: ['0', [Validators.required, MONEY]],
    },
    { validators: limitsInOrder },
  );

  private readonly commission = toSignal(this.form.controls.commission_percent.valueChanges, { initialValue: '10' });
  readonly examplePayout = computed(() => (2 * (1 - Number(this.commission()) / 100)).toFixed(2));

  constructor() {
    this.api.bettingSettings().subscribe({ next: (s) => this.set(s), error: (e) => this.toast.error(e) });
  }

  percent(rate: string): string {
    return String(Number((Number(rate) * 100).toFixed(2)));
  }

  private set(s: BettingSettings): void {
    this.current.set(s);
    const plain = (v: string) => String(Number(v));
    this.form.reset({
      enabled: s.enabled,
      commission_percent: this.percent(s.commission_rate),
      draw_multiplier: plain(s.draw_multiplier),
      min_bet: plain(s.min_bet),
      max_bet: plain(s.max_bet),
      max_fight_stake: plain(s.max_fight_stake),
      starting_balance: plain(s.starting_balance),
    });
  }

  save(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    this.saving.set(true);
    this.api
      .updateBettingSettings({
        enabled: v.enabled,
        commission_rate: (Number(v.commission_percent) / 100).toFixed(4),
        draw_multiplier: Number(v.draw_multiplier).toFixed(2),
        min_bet: Number(v.min_bet).toFixed(2),
        max_bet: Number(v.max_bet).toFixed(2),
        max_fight_stake: Number(v.max_fight_stake).toFixed(2),
        starting_balance: Number(v.starting_balance).toFixed(2),
      })
      .subscribe({
        next: (s) => {
          this.set(s);
          this.saving.set(false);
          this.toast.success('Betting settings saved');
        },
        error: (e) => {
          this.saving.set(false);
          this.toast.error(e);
        },
      });
  }
}

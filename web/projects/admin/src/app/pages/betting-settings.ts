import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators, type AbstractControl, type ValidationErrors } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';

import type { BettingSettings } from '@shared/models';
import { ArenaPointsPipe } from '@shared/pipes';
import { AdminApi, ToastService } from '@shared/services';

const MONEY = Validators.pattern(/^\d+(\.\d{1,2})?$/);

function limitsInOrder(group: AbstractControl): ValidationErrors | null {
  const { min_bet, max_bet, max_fight_stake } = group.value as Record<string, string>;
  if (Number(max_bet) < Number(min_bet)) return { limits: 'The maximum bet must be at least the minimum bet.' };
  if (Number(max_fight_stake) < Number(max_bet)) return { limits: 'The per-fight limit must be at least the maximum bet.' };
  return null;
}

/**
 * Simulated betting configuration. MERON and WALA prices are not set here: they open at 1.83 and
 * move with the pools, always summing to 3.66. Changes apply to new bets and newly opened fights.
 */
@Component({
  selector: 'app-betting-settings',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule, MatSlideToggleModule, ArenaPointsPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form [formGroup]="form" (ngSubmit)="save()" class="flex max-w-3xl flex-col gap-3 py-4" novalidate>
      <p class="m-0 text-sm text-on-surface-variant">
        Players bet from a <strong>balance</strong> granted by the system. It has no cash value and cannot be bought, transferred,
        withdrawn or redeemed. Administrators can only change balances uniformly (Balance Management). Historical legacy points remain in the read-only archive.
      </p>
      <mat-slide-toggle formControlName="enabled">Enable betting in the player portal</mat-slide-toggle>
      <p class="m-0 text-xs text-on-surface-variant">
        Players see the betting board as soon as this is saved. Bets are accepted only on fights where an operator has opened betting:
        <strong>Events → event → fight card → Open betting</strong>. Closing it locks the final odds.
      </p>

      <fieldset class="m-0 mt-2 border-0 p-0">
        <legend class="mb-1 text-sm font-semibold">Odds and returns</legend>
        <p class="m-0 mb-3 text-xs text-on-surface-variant">
          MERON and WALA open at 1.83 and 1.83. Once both sides have points, the two prices move with the pools and always sum to 3.66.
          A heavier pool gets the shorter price, and each bet keeps the odds it accepted. The most a result pays is the two pools.
          DRAW returns a fixed multiplier locked on each bet; when DRAW wins, MERON and WALA stakes are refunded.
        </p>
        <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
          <mat-form-field>
            <mat-label>Pool deduction</mat-label>
            <input matInput formControlName="commission_percent" inputmode="decimal" />
            <span matTextSuffix>%</span>
            <mat-hint>Stored with the fight. It does not change Meron and Wala odds.</mat-hint>
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
              <input matInput [formControlName]="f.key" inputmode="decimal" />
              <mat-hint>{{ f.hint }}</mat-hint>
              <mat-error>Enter an amount with up to two decimals</mat-error>
            </mat-form-field>
          }
        </div>
        @if (current()?.starting_balance === '0.00') {
          <p class="m-0 text-sm text-on-surface-variant">Players start at 0. Add balance to a specific player in Balance Management.</p>
        }
        <mat-slide-toggle formControlName="reset_enabled" class="mt-2">Allow administrators to reset every player to the starting balance</mat-slide-toggle>
        <p class="m-0 text-xs text-on-surface-variant">Resets run only when an administrator starts one in Balance Management, never automatically, and are refused while stakes are open.</p>
        @if (form.errors?.['limits']; as message) { <p class="m-0 text-sm text-error" role="alert">{{ message }}</p> }
      </fieldset>

      @if (current(); as c) {
        <p class="m-0 text-xs text-on-surface-variant">
          Current: deduction {{ percent(c.commission_rate) }}% · DRAW ×{{ c.draw_multiplier }} · bets {{ c.min_bet | arenaPoints }}–{{ c.max_bet | arenaPoints }}
          · {{ c.max_fight_stake | arenaPoints }} per fight · starting balance {{ c.starting_balance | arenaPoints }}
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
    { key: 'starting_balance', label: 'Starting balance', hint: 'Default is 0. A player receives this once, only if it is above 0' },
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
      reset_enabled: [false],
    },
    { validators: limitsInOrder },
  );

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
      reset_enabled: s.reset_policy === 'starting_balance',
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
        reset_policy: v.reset_enabled ? 'starting_balance' : 'disabled',
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

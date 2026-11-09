import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';

import { ArenaPointsPipe } from '@shared/pipes';
import { CommissionApi, ToastService, newIdempotencyKey, toApiError } from '@shared/services';

export interface CashoutDialogData {
  available: string;
  ownBalance: string;
}

type CashoutMode = 'balance' | 'withdraw' | 'convert';

/**
 * Dashboard cashout form. Cashout balance asks approval to take the main wallet down.
 * Withdraw earnings asks approval to take commission down. Convert merges earnings into the main wallet.
 */
@Component({
  selector: 'app-cashout-dialog',
  imports: [MatDialogModule, MatIconModule, ArenaPointsPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="top">
      <h2><mat-icon aria-hidden="true">account_balance_wallet</mat-icon>Withdrawal form</h2>
      <button type="button" class="close" mat-dialog-close aria-label="Close"><mat-icon aria-hidden="true">close</mat-icon></button>
    </header>

    <div class="banner">
      <span class="mark" aria-hidden="true"><mat-icon>account_balance_wallet</mat-icon></span>
      <div>
        <p>{{ selected() === 'balance' ? 'Main wallet' : 'Available earnings' }}</p>
        <strong>₱{{ (selected() === 'balance' ? data.ownBalance : data.available) | arenaPoints }}</strong>
      </div>
    </div>

    <div class="modes" role="tablist" aria-label="Cashout type">
      @for (mode of modes; track mode.id) {
        <button type="button" role="tab" [attr.aria-selected]="selected() === mode.id" [class.on]="selected() === mode.id" (click)="choose(mode.id)">
          <mat-icon aria-hidden="true">{{ mode.icon }}</mat-icon>
          <span>{{ mode.label }}</span>
          @if (selected() === mode.id) { <mat-icon class="tick" aria-hidden="true">check_circle</mat-icon> }
        </button>
      }
    </div>

    <label>
      {{ selected() === 'convert' ? 'Convert amount' : 'Cashout amount' }}
      <input type="text" inputmode="decimal" autocomplete="off" [attr.placeholder]="selected() === 'convert' ? '0.00' : 'Enter amount'" [value]="amount()" (input)="amount.set($any($event.target).value)" />
    </label>

    <p class="details-label">Cashout details</p>
    <p class="details">
      <mat-icon aria-hidden="true">{{ selected() === 'convert' ? 'sync_alt' : 'group' }}</mat-icon>
      {{ selected() === 'convert' ? 'Merge into main wallet' : selected() === 'balance' ? 'This leaves your main wallet now. A decline puts it back' : 'This leaves your earnings now. A decline puts it back' }}
    </p>

    <button type="button" class="submit" [disabled]="busy()" (click)="submit()">
      <mat-icon aria-hidden="true">check</mat-icon>Submit
    </button>
  `,
  styles: `
    :host { display: block; padding: 18px; background: #1a2233; color: #fff; font-family: 'Noto Sans', sans-serif; }
    .top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
    h2 { display: flex; align-items: center; gap: 8px; margin: 0; font: 700 1.15rem/1 'Bebas Neue', sans-serif; letter-spacing: 0.04em; }
    h2 mat-icon { font-size: 26px; width: 26px; height: 26px; }
    .close { width: 36px; height: 36px; border: 0; border-radius: 8px; background: transparent; color: #fff; cursor: pointer; }
    .banner {
      display: flex; align-items: center; gap: 14px; margin-bottom: 14px; padding: 14px 16px; border-radius: 16px;
      background: linear-gradient(90deg, #1b2a44, #1a3a3a);
    }
    .mark { width: 46px; height: 46px; display: grid; place-items: center; border-radius: 50%; color: #7dffa1; box-shadow: inset 0 0 0 2px rgba(125, 255, 161, 0.7); }
    .banner p { margin: 0; font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase; color: rgba(255,255,255,0.7); }
    .banner strong { font: 700 1.7rem/1.1 'Bebas Neue', sans-serif; letter-spacing: 0.02em; }
    .modes { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 14px; }
    .modes button {
      position: relative; min-height: 92px; padding: 10px 6px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.14);
      background: #2a3142; color: #d7dbe4; cursor: pointer; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px;
      font: 700 0.68rem/1.2 'Noto Sans', sans-serif; letter-spacing: 0.04em; text-transform: uppercase;
    }
    .modes button.on { background: #16352a; color: #fff; border-color: #3dff9a; box-shadow: 0 0 0 1px #3dff9a, 0 0 16px rgba(61, 255, 154, 0.25); }
    .tick { position: absolute; top: 6px; right: 6px; color: #3dff9a; font-size: 16px; width: 16px; height: 16px; }
    label { display: flex; flex-direction: column; gap: 6px; font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase; color: rgba(255,255,255,0.65); }
    input {
      height: 46px; padding: 0 12px; border: 0; border-radius: 8px; background: #2a3142; color: #fff; font: 500 0.95rem 'Noto Sans', sans-serif; text-transform: none; letter-spacing: 0;
    }
    .details-label { margin: 14px 0 6px; font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase; color: rgba(255,255,255,0.65); }
    .details {
      display: flex; align-items: center; gap: 8px; min-height: 46px; margin: 0 0 16px; padding: 0 12px; border-radius: 8px; background: #2a3142; color: #e8eaef;
    }
    .submit {
      width: 100%; height: 48px; display: flex; align-items: center; justify-content: center; gap: 6px; border: 0; border-radius: 10px;
      background: #3e60d5; color: #fff; font: 700 0.95rem 'Noto Sans', sans-serif; letter-spacing: 0.06em; text-transform: uppercase; cursor: pointer;
    }
    .submit:disabled { opacity: 0.55; cursor: default; }
    @media (max-width: 599px) {
      .modes { grid-template-columns: 1fr; }
      .modes button { min-height: 64px; flex-direction: row; }
    }
  `,
})
export class CashoutDialog {
  readonly data = inject<CashoutDialogData>(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<CashoutDialog, 'moved' | 'requested' | undefined>);
  private readonly api = inject(CommissionApi);
  private readonly toast = inject(ToastService);

  readonly modes: { id: CashoutMode; label: string; icon: string }[] = [
    { id: 'balance', label: 'Cashout balance', icon: 'payments' },
    { id: 'withdraw', label: 'Withdraw earnings', icon: 'account_balance_wallet' },
    { id: 'convert', label: 'Convert earnings', icon: 'swap_horiz' },
  ];
  readonly selected = signal<CashoutMode>('convert');
  readonly amount = signal(this.data.available);
  readonly busy = signal(false);

  choose(mode: CashoutMode): void {
    this.selected.set(mode);
    this.amount.set(mode === 'balance' ? this.data.ownBalance : this.data.available);
  }

  submit(): void {
    const amount = this.amount().trim();
    const mode = this.selected();
    if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0 || Number(amount) > 1_000_000) {
      this.toast.error('Enter an amount greater than 0, with at most 2 decimal places.');
      return;
    }
    if (mode === 'convert' || mode === 'withdraw') {
      if (Number(amount) > Number(this.data.available || '0')) {
        this.toast.error('That amount is more than the available earnings.');
        return;
      }
    } else if (Number(amount) > Number(this.data.ownBalance || '0')) {
      this.toast.error('That amount is more than your main wallet.');
      return;
    }
    this.busy.set(true);
    const finish = (message: string, result: 'moved' | 'requested') => {
      this.toast.success(message);
      this.ref.close(result);
    };
    const fail = (err: unknown) => { this.busy.set(false); this.toast.error(toApiError(err).message); };
    if (mode === 'convert') {
      this.api.moveEarnings({ amount, idempotency_key: newIdempotencyKey('earnings-move') }).subscribe({
        next: () => finish('Earnings merged into your main wallet.', 'moved'),
        error: fail,
      });
      return;
    }
    this.api.requestBalance({
      amount,
      purpose: mode === 'balance' ? 'cashout' : 'earnings',
      idempotency_key: newIdempotencyKey('balance-request'),
    }).subscribe({
      next: () => finish('Request sent. Loaders and Super Admins are notified to approve it.', 'requested'),
      error: fail,
    });
  }
}

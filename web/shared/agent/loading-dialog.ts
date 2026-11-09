import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';

import { ArenaPointsPipe } from '@shared/pipes';
import { AgentUsersApi, CommissionApi, MyUserRow, ToastService, newIdempotencyKey, toApiError } from '@shared/services';

export interface LoadingDialogData {
  balance: string;
  /** User chosen from My Users. The recipient field starts on this account. */
  recipient?: MyUserRow;
}

type LoadMethod = 'send' | 'withdraw' | 'commission';

/** Loading form opened from the Main Wallet card. */
@Component({
  selector: 'app-loading-dialog',
  imports: [MatDialogModule, MatIconModule, ArenaPointsPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="top">
      <h2><mat-icon aria-hidden="true">calculate</mat-icon>Loading form</h2>
      <button type="button" class="close" mat-dialog-close aria-label="Close"><mat-icon aria-hidden="true">close</mat-icon></button>
    </header>

    <div class="banner">
      <span class="mark" aria-hidden="true"><mat-icon>account_balance_wallet</mat-icon></span>
      <div>
        <p>Main wallet</p>
        <strong>₱{{ data.balance | arenaPoints }}</strong>
      </div>
    </div>

    <p class="label">Transaction method</p>
    <div class="modes" role="tablist">
      @for (mode of modes; track mode.id) {
        <button type="button" role="tab" [attr.aria-selected]="method() === mode.id" [class.on]="method() === mode.id" (click)="method.set(mode.id)">
          <mat-icon aria-hidden="true">{{ mode.icon }}</mat-icon>
          <span>{{ mode.label }}</span>
          @if (method() === mode.id) { <mat-icon class="tick" aria-hidden="true">check_circle</mat-icon> }
        </button>
      }
    </div>

    <div class="combo" (focusout)="closeIfLeft($event)">
      <p class="label">Recipient</p>
      <span class="field">
        <mat-icon aria-hidden="true">person</mat-icon>
        <input role="combobox" aria-autocomplete="list" [attr.aria-expanded]="open()" placeholder="Type Recipient Account" [value]="query()" (focus)="open.set(true)" (input)="onQuery($event)" />
        <button type="button" class="chev" (click)="open.set(!open())" aria-label="Show recipients"><mat-icon aria-hidden="true">{{ open() ? 'expand_less' : 'expand_more' }}</mat-icon></button>
      </span>
      @if (open()) {
        <ul class="menu" role="listbox">
          @for (person of matches(); track person.id) {
            <li role="option" [attr.aria-selected]="person.id === recipientId()">
              <button type="button" (click)="pick(person)">{{ person.username }} (₱{{ (person.balance ?? '0.00') | arenaPoints }})</button>
            </li>
          } @empty {
            <li class="none">No accounts</li>
          }
        </ul>
      }
    </div>

    <label>
      Amount
      <span class="field">
        <span class="peso" aria-hidden="true">₱</span>
        <input type="text" inputmode="decimal" autocomplete="off" placeholder="0.00" [value]="amount()" (input)="amount.set($any($event.target).value)" />
      </span>
    </label>

    <label>
      Details
      <span class="field">
        <mat-icon aria-hidden="true">description</mat-icon>
        <input type="text" maxlength="300" placeholder="(Optional)" [value]="details()" (input)="details.set($any($event.target).value)" />
      </span>
    </label>

    <button type="button" class="submit" [disabled]="busy()" (click)="submit()">
      <mat-icon aria-hidden="true">check</mat-icon>Submit
    </button>
  `,
  styles: `
    :host { display: block; padding: 18px; background: #1a2233; color: #fff; font-family: 'Noto Sans', sans-serif; }
    .top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
    h2 { display: flex; align-items: center; gap: 8px; margin: 0; font: 700 1.15rem/1 'Bebas Neue', sans-serif; letter-spacing: 0.04em; }
    .close { width: 36px; height: 36px; border: 0; border-radius: 8px; background: transparent; color: #fff; cursor: pointer; }
    .banner { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; padding: 14px 16px; border-radius: 16px; background: linear-gradient(90deg, #1b2a44, #1a3a3a); }
    .mark { width: 46px; height: 46px; display: grid; place-items: center; border-radius: 50%; color: #7dffa1; box-shadow: inset 0 0 0 2px rgba(125, 255, 161, 0.7); }
    .banner p, .label, label { margin: 0; font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase; color: rgba(255,255,255,0.65); }
    .banner strong { font: 700 1.7rem/1.1 'Bebas Neue', sans-serif; }
    .label { margin: 0 0 8px; }
    .modes { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 14px; }
    .modes button {
      position: relative; min-height: 88px; padding: 8px 4px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.14);
      background: #2a3142; color: #d7dbe4; cursor: pointer; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px;
      font: 700 0.68rem/1.2 'Noto Sans', sans-serif; letter-spacing: 0.03em; text-transform: uppercase;
    }
    .modes button.on { background: #16352a; color: #fff; border-color: #3dff9a; box-shadow: 0 0 0 1px #3dff9a, 0 0 16px rgba(61, 255, 154, 0.25); }
    .tick { position: absolute; top: 6px; right: 6px; color: #3dff9a; font-size: 16px; width: 16px; height: 16px; }
    label { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
    .field { display: flex; align-items: center; gap: 8px; min-height: 46px; padding: 0 12px; border-radius: 8px; background: #2a3142; text-transform: none; letter-spacing: 0; }
    input { flex: 1; min-width: 0; height: 46px; border: 0; background: transparent; color: #fff; font: 500 0.95rem 'Noto Sans', sans-serif; }
    input::placeholder { color: rgba(255,255,255,0.55); }
    .combo { position: relative; margin-bottom: 12px; }
    .chev { display: grid; place-items: center; width: 32px; height: 32px; border: 0; background: transparent; color: rgba(255,255,255,0.8); cursor: pointer; }
    .menu {
      position: absolute; z-index: 5; top: calc(100% + 4px); left: 0; right: 0; max-height: 240px; margin: 0; padding: 6px 0; overflow: auto;
      list-style: none; border-radius: 8px; background: #2a3038; box-shadow: 0 12px 28px rgba(0,0,0,0.45);
    }
    .menu button { width: 100%; padding: 10px 14px; border: 0; background: transparent; color: #eee; text-align: start; font: 500 0.95rem 'Noto Sans', sans-serif; cursor: pointer; }
    .menu button:hover, .menu [aria-selected='true'] button { background: rgba(255,255,255,0.06); }
    .none { padding: 10px 14px; color: rgba(255,255,255,0.55); }
    .peso { color: rgba(255,255,255,0.7); }
    .submit {
      width: 100%; height: 48px; margin-top: 4px; display: flex; align-items: center; justify-content: center; gap: 6px; border: 0; border-radius: 10px;
      background: #3e60d5; color: #fff; font: 700 0.95rem 'Noto Sans', sans-serif; letter-spacing: 0.06em; text-transform: uppercase; cursor: pointer;
    }
    .submit:disabled { opacity: 0.55; cursor: default; }
    @media (max-width: 599px) { .modes { grid-template-columns: 1fr; } .modes button { min-height: 56px; flex-direction: row; } }
  `,
})
export class LoadingDialog {
  readonly data = inject<LoadingDialogData>(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<LoadingDialog, boolean>);
  private readonly api = inject(CommissionApi);
  private readonly users = inject(AgentUsersApi);
  private readonly toast = inject(ToastService);

  readonly modes: { id: LoadMethod; label: string; icon: string }[] = [
    { id: 'send', label: 'Send funds', icon: 'add_card' },
    { id: 'withdraw', label: 'Withdraw funds', icon: 'payments' },
    { id: 'commission', label: 'Comm to comm', icon: 'swap_horiz' },
  ];
  readonly method = signal<LoadMethod>('send');
  readonly recipientId = signal('');
  readonly query = signal('');
  readonly open = signal(false);
  readonly amount = signal('');
  readonly details = signal('');
  readonly recipients = signal<MyUserRow[]>([]);
  readonly busy = signal(false);
  readonly matches = computed(() => {
    const term = this.query().trim().toLowerCase();
    const rows = this.recipients();
    return term ? rows.filter((row) => row.username.toLowerCase().includes(term)) : rows;
  });

  constructor() {
    const preset = this.data.recipient;
    if (preset) {
      this.recipientId.set(preset.id);
      this.query.set(preset.username);
    }
    this.users.list({ page: 1, page_size: 100 }).subscribe({
      next: (page) => {
        const items = page.items;
        this.recipients.set(preset && !items.some((row) => row.id === preset.id) ? [preset, ...items] : items);
      },
      error: () => this.recipients.set(preset ? [preset] : []),
    });
  }

  onQuery(event: Event): void {
    this.query.set((event.target as HTMLInputElement).value);
    this.recipientId.set('');
    this.open.set(true);
  }

  pick(person: MyUserRow): void {
    this.recipientId.set(person.id);
    this.query.set(person.username);
    this.open.set(false);
  }

  closeIfLeft(event: FocusEvent): void {
    const next = event.relatedTarget as Node | null;
    if (!next || !(event.currentTarget as HTMLElement).contains(next)) this.open.set(false);
  }

  submit(): void {
    const amount = this.amount().trim();
    const recipient = this.recipientId();
    if (!recipient) {
      this.toast.error('Choose a recipient account.');
      return;
    }
    if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0 || Number(amount) > 1_000_000) {
      this.toast.error('Enter an amount greater than 0, with at most 2 decimal places.');
      return;
    }
    this.busy.set(true);
    const details = this.details().trim();
    this.api.loadFunds({
      method: this.method(), recipient_id: recipient, amount, idempotency_key: newIdempotencyKey('load'),
      ...(details ? { details } : {}),
    }).subscribe({
      next: (row) => {
        const verb = this.method() === 'send' ? 'sent to' : this.method() === 'withdraw' ? 'withdrawn from' : 'moved to';
        this.toast.success(`${amount} ${verb} ${row.recipient_username}.`);
        this.ref.close(true);
      },
      error: (err: unknown) => { this.busy.set(false); this.toast.error(toApiError(err).message); },
    });
  }
}

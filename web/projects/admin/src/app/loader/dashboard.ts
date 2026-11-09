import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import { ConfirmService, FieldErrorComponent, PageHeaderComponent, StatusChipComponent } from '@shared/components';
import type { AccountStatus, AccountType, LoaderAccount, LoaderGrant } from '@shared/models';
import { ArenaPointsPipe, LabelPipe } from '@shared/pipes';
import { AdminApi, ToastService, newIdempotencyKey, toApiError } from '@shared/services';

const AMOUNT = /^\d{1,7}(\.\d{1,2})?$/;

@Component({
  selector: 'app-loader-dashboard',
  imports: [
    DatePipe, ReactiveFormsModule, ArenaPointsPipe, LabelPipe, MatButtonModule, MatFormFieldModule, MatIconModule,
    MatInputModule, MatSelectModule, PageHeaderComponent, StatusChipComponent, FieldErrorComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Loader" subtitle="Allocate the points an administrator has assigned to you." />
    <p class="m-0 mb-4 flex items-start gap-2 rounded-xl bg-secondary-container p-3 text-sm text-on-secondary-container" role="note">
      <mat-icon aria-hidden="true">info</mat-icon>
      Points are for simulated play only. You can only give out the balance an administrator has already allocated. You cannot create points, edit accounts, or change history.
    </p>

    <section class="mb-4 rounded-2xl border border-outline-variant p-4" aria-labelledby="allocation-title">
      <h2 id="allocation-title" class="m-0 text-sm text-on-surface-variant">Available allocation</h2>
      <p class="m-0 text-2xl font-semibold tabular-nums" data-testid="allocation">{{ allocation() | arenaPoints }}</p>
    </section>

    <nav class="mb-4 flex flex-wrap gap-2" aria-label="Loader sections">
      <button mat-stroked-button type="button" (click)="show('')">Balance management</button>
      <button mat-stroked-button type="button" (click)="show('master_agent')">Master agents</button>
      <button mat-stroked-button type="button" (click)="show('agent')">Agents</button>
      <button mat-stroked-button type="button" (click)="show('player')">Players</button>
      <a mat-stroked-button href="#history-title">Balance history</a>
    </nav>
    <section class="mb-6 rounded-2xl border border-outline-variant p-4" aria-labelledby="accounts-title">
      <h2 id="accounts-title" class="m-0 mb-3 text-base font-semibold">{{ sectionTitle() }}</h2>
      <form class="mb-3 grid grid-cols-1 gap-3 sm:grid-cols-3" (submit)="$event.preventDefault(); loadAccounts()">
        <mat-form-field subscriptSizing="dynamic"><mat-label>Username</mat-label>
          <input matInput [formControl]="query" data-testid="loader-search" />
        </mat-form-field>
        <mat-form-field subscriptSizing="dynamic"><mat-label>Account type</mat-label>
          <mat-select [formControl]="typeFilter">
            <mat-option value="">All</mat-option>
            <mat-option value="master_agent">Master agents</mat-option>
            <mat-option value="agent">Agents</mat-option>
            <mat-option value="player">Players</mat-option>
          </mat-select>
        </mat-form-field>
        <mat-form-field subscriptSizing="dynamic"><mat-label>Status</mat-label>
          <mat-select [formControl]="statusFilter">
            <mat-option value="">All</mat-option>
            <mat-option value="active">Active</mat-option>
            <mat-option value="suspended">Suspended</mat-option>
          </mat-select>
        </mat-form-field>
      </form>
      @if (listError()) { <p class="text-sm text-error" role="alert">{{ listError() }}</p> }
      <div class="overflow-x-auto">
        <table class="w-full min-w-[44rem] border-collapse text-sm">
          <thead>
            <tr class="text-left text-on-surface-variant">
              <th class="py-2 pr-3 font-medium">Username</th>
              <th class="py-2 pr-3 font-medium">Account type</th>
              <th class="py-2 pr-3 font-medium">Parent</th>
              <th class="py-2 pr-3 font-medium">Available balance</th>
              <th class="py-2 pr-3 font-medium">Status</th>
              <th class="py-2 font-medium">Balance</th>
            </tr>
          </thead>
          <tbody>
            @for (row of accounts(); track row.id) {
              <tr class="border-t border-outline-variant" [attr.data-username]="row.username">
                <td class="py-2 pr-3">{{ row.username }}</td>
                <td class="py-2 pr-3">{{ row.account_type | label }}</td>
                <td class="py-2 pr-3">{{ row.parent_username || '—' }}</td>
                <td class="py-2 pr-3 tabular-nums">{{ row.balance | arenaPoints }}</td>
                <td class="py-2 pr-3"><app-status-chip [status]="row.status" /></td>
                <td class="py-2">
                  <button mat-stroked-button type="button" [disabled]="row.status !== 'active' || busy()" (click)="choose(row)">Add balance</button>
                </td>
              </tr>
            } @empty {
              <tr><td class="py-4 text-on-surface-variant" colspan="6">No accounts match these filters.</td></tr>
            }
          </tbody>
        </table>
      </div>
    </section>

    @if (selected(); as account) {
      <section class="mb-6 rounded-2xl border border-outline-variant p-4" aria-labelledby="grant-title" data-testid="grant-panel">
        <h2 id="grant-title" class="m-0 mb-3 text-base font-semibold">Add balance</h2>
        <dl class="m-0 mb-3 grid grid-cols-1 gap-2 text-sm sm:grid-cols-3">
          <div><dt class="text-on-surface-variant">Recipient</dt><dd class="m-0 font-semibold">{{ account.username }} · {{ account.account_type | label }}</dd></div>
          <div><dt class="text-on-surface-variant">Current balance</dt><dd class="m-0 tabular-nums">{{ account.balance | arenaPoints }}</dd></div>
          <div><dt class="text-on-surface-variant">Your allocation</dt><dd class="m-0 tabular-nums">{{ allocation() | arenaPoints }}</dd></div>
        </dl>
        <form class="grid grid-cols-1 gap-3 sm:grid-cols-[12rem_1fr_auto] sm:items-start" [formGroup]="grantForm" (ngSubmit)="submit()">
          <mat-form-field subscriptSizing="dynamic"><mat-label>Points</mat-label>
            <input matInput formControlName="amount" inputmode="decimal" data-testid="grant-amount" />
            <mat-error><app-field-error [control]="grantForm.controls.amount" /></mat-error>
          </mat-form-field>
          <mat-form-field subscriptSizing="dynamic"><mat-label>Reason</mat-label>
            <input matInput formControlName="reason" maxlength="300" data-testid="grant-reason" />
            <mat-error><app-field-error [control]="grantForm.controls.reason" /></mat-error>
          </mat-form-field>
          <button mat-flat-button type="submit" class="sm:mt-1" [disabled]="busy()">Review</button>
        </form>
      </section>
    }

    <section class="rounded-2xl border border-outline-variant p-4" aria-labelledby="history-title">
      <h2 id="history-title" class="m-0 mb-3 text-base font-semibold">Balance history</h2>
      <div class="overflow-x-auto">
        <table class="w-full min-w-[40rem] border-collapse text-sm">
          <thead>
            <tr class="text-left text-on-surface-variant">
              <th class="py-2 pr-3 font-medium">When</th>
              <th class="py-2 pr-3 font-medium">Reference</th>
              <th class="py-2 pr-3 font-medium">Recipient</th>
              <th class="py-2 pr-3 font-medium">Amount</th>
              <th class="py-2 font-medium">Reason</th>
            </tr>
          </thead>
          <tbody>
            @for (row of history(); track row.id) {
              <tr class="border-t border-outline-variant">
                <td class="py-2 pr-3">{{ row.created_at | date:'medium' }}</td>
                <td class="py-2 pr-3 font-mono text-xs">{{ row.reference }}</td>
                <td class="py-2 pr-3">{{ row.recipient_username }} · {{ row.recipient_type | label }}</td>
                <td class="py-2 pr-3 tabular-nums">{{ row.amount | arenaPoints }}</td>
                <td class="py-2">{{ row.reason }}</td>
              </tr>
            } @empty {
              <tr><td class="py-4 text-on-surface-variant" colspan="5">No allocations yet.</td></tr>
            }
          </tbody>
        </table>
      </div>
    </section>
  `,
})
export class LoaderDashboard {
  private readonly api = inject(AdminApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);

  readonly sectionTitle = signal('Balance management');
  readonly allocation = signal('0');
  readonly accounts = signal<LoaderAccount[]>([]);
  readonly history = signal<LoaderGrant[]>([]);
  readonly selected = signal<LoaderAccount | null>(null);
  readonly listError = signal<string | null>(null);
  readonly busy = signal(false);
  readonly query = new FormControl('', { nonNullable: true });
  readonly typeFilter = new FormControl<AccountType | ''>('', { nonNullable: true });
  readonly statusFilter = new FormControl<AccountStatus | ''>('', { nonNullable: true });
  readonly grantForm = new FormGroup({
    amount: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(AMOUNT)] }),
    reason: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.minLength(5), Validators.maxLength(300)] }),
  });

  constructor() {
    this.typeFilter.valueChanges.subscribe(() => this.loadAccounts());
    this.statusFilter.valueChanges.subscribe(() => this.loadAccounts());
    this.query.valueChanges.subscribe(() => this.loadAccounts());
    this.loadAllocation();
    this.loadAccounts();
    this.loadHistory();
  }

  show(type: AccountType | ''): void {
    this.sectionTitle.set(type === 'master_agent' ? 'Master agents' : type === 'agent' ? 'Agents' : type === 'player' ? 'Players' : 'Balance management');
    this.typeFilter.setValue(type);
  }

  choose(row: LoaderAccount): void {
    this.selected.set(row);
    this.grantForm.reset({ amount: '', reason: '' });
  }

  loadAllocation(): void {
    this.api.loaderAllocation().subscribe({
      next: (row) => this.allocation.set(row.balance),
      error: (e: unknown) => this.toast.error(e),
    });
  }

  loadAccounts(): void {
    this.api.loaderAccounts({
      q: this.query.value.trim() || null,
      account_type: this.typeFilter.value || null,
      status: this.statusFilter.value || null,
      page_size: 100,
    }).subscribe({
      next: (page) => {
        this.listError.set(null);
        this.accounts.set(page.items);
        const current = this.selected();
        if (current) this.selected.set(page.items.find((row) => row.id === current.id) ?? current);
      },
      error: (e: unknown) => this.listError.set(toApiError(e).message),
    });
  }

  loadHistory(): void {
    this.api.loaderHistory({ page_size: 50 }).subscribe({
      next: (page) => this.history.set(page.items),
      error: (e: unknown) => this.toast.error(e),
    });
  }

  async submit(): Promise<void> {
    const account = this.selected();
    if (!account || this.grantForm.invalid || this.busy()) {
      this.grantForm.markAllAsTouched();
      return;
    }
    const { amount, reason } = this.grantForm.getRawValue();
    const ok = await this.confirm.ask({
      title: `Add balance to ${account.username}?`,
      message: `${account.username} (${account.account_type.replace(/_/g, ' ')}) currently has ${account.balance}. Add ${amount} points. Your allocation afterwards is reduced by the same amount. Reason: ${reason}`,
      confirmText: 'Add balance',
    });
    if (ok === null) return;
    this.busy.set(true);
    this.api.loaderGrant({
      target_user_id: account.id,
      amount,
      reason,
      idempotency_key: newIdempotencyKey('loader'),
    }).subscribe({
      next: (row) => {
        this.busy.set(false);
        this.toast.success(`Added ${amount} to ${row.recipient_username}. Reference ${row.reference}.`);
        this.grantForm.reset({ amount: '', reason: '' });
        this.loadAllocation();
        this.loadAccounts();
        this.loadHistory();
      },
      error: (e: unknown) => {
        this.busy.set(false);
        this.toast.error(e);
      },
    });
  }
}

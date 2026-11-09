import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, FormGroupDirective, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatTabsModule } from '@angular/material/tabs';
import { Observable, catchError, debounceTime, distinctUntilChanged, of, startWith, switchMap } from 'rxjs';

import {
  CellDefDirective, Column, ConfirmService, DataTableComponent, FieldErrorComponent, PageHeaderComponent, StatCardComponent, StatusChipComponent,
} from '@shared/components';
import type {
  AccountType, BalanceAccount, BalanceAccountDetail, BalanceOperation, BalanceOperationKind, BalanceSummary, PlayEntryKind, PlayWalletEntry, TenantProfile,
} from '@shared/models';
import { ArenaPointsPipe, LabelPipe, formatArenaPoints, toCents } from '@shared/pipes';
import { AdminApi, AuthStore, PagedList, RealtimeService, ToastService, newIdempotencyKey, toApiError } from '@shared/services';

export const ENTRY_LABEL: Record<PlayEntryKind, string> = {
  grant: 'Starting balance', stake: 'Bet placed', payout: 'Win credited', refund: 'Refund', resettlement: 'Result correction',
  account_grant: 'Account grant', uniform_grant: 'Uniform grant', reset: 'Reset', correction: 'Balance correction',
  transfer: 'Balance transfer', earnings: 'Earnings moved to play balance',
};
const OPERATION_LABEL: Record<BalanceOperationKind, string> = {
  account_grant: 'Account grant', uniform_grant: 'Uniform grant', reset: 'Reset', correction: 'Correction',
};
/** Administrator postings that a reviewed correction may reverse. Play results are corrected through results. */
const CORRECTABLE: PlayEntryKind[] = ['grant', 'account_grant', 'uniform_grant', 'reset'];
const ROLE_FILTERS: { value: AccountType | ''; label: string }[] = [
  { value: '', label: 'All' }, { value: 'master_agent', label: 'Master agents' }, { value: 'agent', label: 'Agents' }, { value: 'player', label: 'Players' },
];
const LOADER_FILTER: { value: AccountType; label: string } = { value: 'loader', label: 'Loaders' };
const AMOUNT = /^\d{1,7}(\.\d{1,2})?$/;

/**
 * Balance Management. Administrators add balance to one selected player; master agents and agents see
 * their downline read-only. Scope, permissions, tenant and balances are always decided by the API: this
 * page never sends a sender, tenant or balance, and there is no transfer between accounts.
 * Balances are simulated and have no cash value.
 */
@Component({
  selector: 'app-balance-management',
  imports: [
    DatePipe, ReactiveFormsModule, ArenaPointsPipe, LabelPipe, MatAutocompleteModule, MatButtonModule, MatButtonToggleModule, MatFormFieldModule,
    MatIconModule, MatInputModule, MatSelectModule, MatTabsModule, PageHeaderComponent, StatCardComponent, StatusChipComponent, DataTableComponent,
    CellDefDirective, FieldErrorComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .controls { display: grid; grid-template-columns: minmax(16rem, 28rem) 1fr; gap: 0.75rem 1rem; align-items: start; margin: 1rem 0; }
    .controls > * { min-width: 0; }
    .roles { max-width: 100%; overflow-x: auto; }
    .identity { display: block; overflow-wrap: anywhere; } .detail { display: block; font-size: .75rem; color: var(--text-secondary); }
    .selected { display: flex; flex-wrap: wrap; gap: 0.5rem 1.25rem; align-items: center; padding: 0.75rem 1rem; margin-bottom: 1rem;
      border-radius: 0.9rem; border: 1px solid var(--mat-sys-outline-variant); background: var(--surface); }
    .selected strong { font-size: 1.05rem; } .selected dl { display: flex; flex-wrap: wrap; gap: 0.25rem 1.25rem; margin: 0; }
    .selected dt { font-size: 0.72rem; color: var(--text-secondary); } .selected dd { margin: 0; font-variant-numeric: tabular-nums; }
    .actions { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 19rem), 1fr)); gap: 1rem; margin: 1rem 0; }
    .card { padding: 1rem; border-radius: 1rem; border: 1px solid var(--mat-sys-outline-variant); background: var(--surface); display: flex; flex-direction: column; gap: 0.5rem; }
    .card h3 { margin: 0; font-size: 0.95rem; } .card p { margin: 0; font-size: 0.8rem; color: var(--text-secondary); }
    .card form { display: flex; flex-direction: column; gap: 0.25rem; } .card button[type=submit] { align-self: flex-start; }
    .date-filter { display: inline-flex; gap: 0.4rem; align-items: center; font-size: 0.8rem; color: var(--text-secondary); }
    .date-filter input { min-height: 2.25rem; padding: 0 0.4rem; border-radius: 0.5rem; border: 1px solid var(--mat-sys-outline-variant); background: transparent; color: inherit; font: inherit; }
    .num { font-variant-numeric: tabular-nums; } .plus { color: var(--success-text); } .minus { color: var(--text-secondary); }
    .ref { font-family: ui-monospace, monospace; font-size: 0.75rem; overflow-wrap: anywhere; }
    .row-actions { display: inline-flex; gap: 0.25rem; flex-wrap: wrap; justify-content: flex-end; }
    .muted { color: var(--text-secondary); font-size: 0.8rem; }
    @media (max-width: 900px) { .controls { grid-template-columns: 1fr; } }
  `,
  template: `
    <app-page-header title="Balance Management" [subtitle]="subtitle()" />
    <p class="m-0 mb-4 flex items-start gap-2 rounded-xl bg-secondary-container p-3 text-sm text-on-secondary-container" role="note" data-testid="no-cash-value">
      <mat-icon aria-hidden="true">info</mat-icon>Balances are for simulated play only. They have no cash value and cannot be bought, transferred, withdrawn or redeemed.
    </p>

    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <app-stat-card label="Accounts in view" icon="group" [loading]="!summary()" [value]="summary()?.accounts ?? null" />
      <app-stat-card label="Players" icon="sports_esports" [loading]="!summary()" [value]="summary()?.participants ?? null" />
      <app-stat-card label="Available balance (total)" icon="account_balance_wallet" [loading]="!summary()" [value]="summary() ? points(summary()!.total_balance) : null" />
      @if (isLoader()) {
        <app-stat-card label="Your allocation" icon="account_balance_wallet" [loading]="allocation() === null" [value]="allocation() === null ? null : points(allocation()!)" />
      }
      @if (summary()?.pending_corrections !== null) {
        <app-stat-card label="Corrections awaiting review" icon="rule" [tone]="summary()?.pending_corrections ? 'warning' : 'neutral'" [loading]="!summary()"
                       [value]="summary()?.pending_corrections ?? null" />
      }
    </div>

    <div class="controls">
      <mat-form-field class="w-full" subscriptSizing="dynamic">
        <mat-label>Account</mat-label>
        <input matInput [formControl]="search" [matAutocomplete]="auto" placeholder="Name or username" data-testid="account-search" />
        <mat-autocomplete #auto="matAutocomplete" [displayWith]="display" (optionSelected)="select($event.option.value)">
          @for (a of options(); track a.id) {
            <mat-option [value]="a">
              <span class="identity">{{ a.username }} · {{ a.full_name }}</span>
              <span class="detail">{{ a.account_type | label }}{{ a.parent_username ? ' · under ' + a.parent_username : '' }} · {{ a.balance | arenaPoints }}</span>
            </mat-option>
          }
        </mat-autocomplete>
        <mat-hint>Only accounts you are authorized to see are listed.</mat-hint>
      </mat-form-field>
      <div class="flex min-w-0 flex-wrap items-center gap-3">
        <mat-button-toggle-group class="roles" [value]="role()" (change)="setRole($event.value)" aria-label="Filter by role" hideSingleSelectionIndicator>
          @for (r of roles(); track r.value) { <mat-button-toggle [value]="r.value">{{ r.label }}</mat-button-toggle> }
        </mat-button-toggle-group>
        <label class="date-filter">From (UTC) <input type="date" aria-label="From date (UTC)" [value]="from()" (change)="setDate('date_from', $any($event.target).value)" /></label>
        <label class="date-filter">To (UTC) <input type="date" aria-label="To date (UTC)" [value]="to()" (change)="setDate('date_to', $any($event.target).value)" /></label>
      </div>
    </div>

    @if (selected(); as s) {
      <section class="selected" aria-label="Selected account" data-testid="selected-account">
        <div class="flex flex-wrap items-baseline gap-x-2"><strong>{{ s.account.username }}</strong><span class="muted">{{ s.account.account_type | label }}{{ s.account.parent_username ? ' · under ' + s.account.parent_username : '' }}</span></div>
        <dl>
          <div><dt>Available balance</dt><dd data-field="balance">{{ s.account.balance | arenaPoints }}</dd></div>
          <div><dt>Starting balance</dt><dd>{{ s.statement.granted | arenaPoints }}</dd></div>
          <div><dt>Added to this account</dt><dd>{{ signed(s.statement.account_grants ?? '0') }}</dd></div>
          <div><dt>Uniform grants</dt><dd>{{ signed(s.statement.uniform_grants ?? '0') }}</dd></div>
          <div><dt>Resets</dt><dd>{{ signed(s.statement.resets ?? '0') }}</dd></div>
          <div><dt>Corrections</dt><dd>{{ signed(s.statement.balance_corrections ?? '0') }}</dd></div>
          <div><dt>Play result</dt><dd>{{ signed(s.statement.net_result) }}</dd></div>
        </dl>
        <span class="flex-1"></span>
        @if (s.account.account_type !== 'player') {
          <button mat-stroked-button type="button" (click)="showDownline(s.account)">Show their accounts</button>
        }
        <button mat-button type="button" (click)="clearSelection()">Clear</button>
      </section>
    }

    @if (isLoader()) {
      <div class="actions" data-testid="loader-grant">
        <section class="card" aria-labelledby="loader-grant-title">
          <h3 id="loader-grant-title">Add to this account</h3>
          @if (loaderTarget(); as account) {
            <p>{{ account.username }} currently has {{ account.balance | arenaPoints }}. The amount comes out of your allocation ({{ allocation() === null ? '…' : points(allocation()!) }}). It is not created, and the history cannot be edited.</p>
            <form [formGroup]="loaderGrantForm" #loaderGrantDir="ngForm" (ngSubmit)="loaderGrant()">
              <mat-form-field subscriptSizing="dynamic"><mat-label>Amount</mat-label>
                <input matInput formControlName="amount" inputmode="decimal" autocomplete="off" data-testid="loader-grant-amount" />
                <mat-error><app-field-error [control]="loaderGrantForm.controls.amount" /></mat-error>
              </mat-form-field>
              <mat-form-field subscriptSizing="dynamic"><mat-label>Reason</mat-label>
                <input matInput formControlName="reason" maxlength="300" data-testid="loader-grant-reason" />
                <mat-error><app-field-error [control]="loaderGrantForm.controls.reason" /></mat-error>
              </mat-form-field>
              <button mat-flat-button type="submit" [disabled]="busy() || account.status !== 'active'" data-testid="loader-grant-submit">Add to {{ account.username }}</button>
            </form>
          } @else if (selected()) {
            <p>Select a master agent, agent, or player. Those are the only accounts a Loader can fund.</p>
          } @else {
            <p>Select a master agent, agent, or player above.</p>
          }
        </section>
      </div>
    }

    @if (canManage()) {
      <div class="actions" data-testid="balance-actions">
        <section class="card" aria-labelledby="account-grant-title">
          <h3 id="account-grant-title">Add to this account</h3>
          @if (selected()?.account?.account_type === 'player' || selected()?.account?.account_type === 'agent' || selected()?.account?.account_type === 'master_agent' || selected()?.account?.account_type === 'loader') {
            <p>{{ selected()!.account.account_type === 'loader' ? 'Adds the amount to this Loader’s allocation. They can only give out points from that balance.' : 'Adds the amount to ' + selected()!.account.username + ' only. Every other account stays the same.' }}</p>
            <form [formGroup]="accountGrantForm" #accountGrantDir="ngForm" (ngSubmit)="accountGrant()">
              <mat-form-field subscriptSizing="dynamic"><mat-label>Amount</mat-label>
                <input matInput formControlName="amount" inputmode="decimal" autocomplete="off" data-testid="account-grant-amount" />
                <mat-error><app-field-error [control]="accountGrantForm.controls.amount" /></mat-error>
              </mat-form-field>
              <mat-form-field subscriptSizing="dynamic"><mat-label>Remarks (optional)</mat-label><input matInput formControlName="remarks" maxlength="300" /></mat-form-field>
              <button mat-flat-button type="submit" [disabled]="busy()" data-testid="account-grant-submit">Add to {{ selected()!.account.username }}</button>
            </form>
          } @else if (selected()) {
            <p>Select a player, agent, master agent, or loader. Play balance is held by those accounts.</p>
          } @else {
            <p>Select a player above. The amount is added only to that account.</p>
          }
        </section>

        <section class="card" aria-labelledby="reset-title">
          <h3 id="reset-title">Reset balances</h3>
          @if (summary()?.reset_policy === 'starting_balance') {
            <p>Sets every active player to the starting balance of {{ points(summary()!.starting_balance) }}. Refused while any stake is open.</p>
            <button mat-stroked-button type="button" class="destructive-action" [disabled]="busy()" (click)="reset()">Reset every player</button>
          } @else {
            <p>Resets are disabled by the reset policy. Change it in Betting settings.</p>
          }
        </section>

        <section class="card" aria-labelledby="correction-title">
          <h3 id="correction-title">Propose a correction</h3>
          <p>Reverses an administrator posting made in error. A different administrator must approve it.</p>
          @if (selected()?.account?.account_type === 'player') {
            <form [formGroup]="correctionForm" #correctionDir="ngForm" (ngSubmit)="propose()">
              <mat-form-field subscriptSizing="dynamic"><mat-label>Basis</mat-label>
                <mat-select formControlName="basis" data-testid="correction-basis">
                  @for (e of correctable(); track e.id) {
                    <mat-option [value]="'entry:' + e.id">{{ entryText(e.kind) }} {{ signed(e.delta) }} · {{ e.created_at | date: 'MMM d, HH:mm' : 'UTC' }}</mat-option>
                  }
                  @for (g of grants(); track g.id) {
                    <mat-option [value]="'grant:' + g.id">Missed {{ g.reference }} (+{{ points(g.amount) }})</mat-option>
                  }
                </mat-select>
                <mat-error><app-field-error [control]="correctionForm.controls.basis" /></mat-error>
              </mat-form-field>
              @if (correctionForm.controls.basis.value.startsWith('entry:')) {
                <mat-form-field subscriptSizing="dynamic"><mat-label>Amount (empty = all that remains)</mat-label>
                  <input matInput formControlName="amount" inputmode="decimal" autocomplete="off" />
                  <mat-error><app-field-error [control]="correctionForm.controls.amount" /></mat-error>
                </mat-form-field>
              }
              <mat-form-field subscriptSizing="dynamic"><mat-label>Verified reason</mat-label>
                <input matInput formControlName="remarks" maxlength="300" data-testid="correction-reason" />
                <mat-error><app-field-error [control]="correctionForm.controls.remarks" /></mat-error>
              </mat-form-field>
              <button mat-stroked-button type="submit" [disabled]="busy()">Submit for review</button>
            </form>
          } @else {
            <p class="muted">Select a player above to correct one of their administrator postings.</p>
          }
        </section>
      </div>
    }

    <mat-tab-group class="mt-2" mat-stretch-tabs="false" animationDuration="0ms" [(selectedIndex)]="tab">
      <mat-tab label="Accounts">
        <app-data-table class="mt-4 block" [columns]="accountColumns" [rows]="accounts.rows()" [total]="accounts.total()" [page]="accounts.page()" [pageSize]="accounts.pageSize()"
                        [loading]="accounts.loading()" [error]="accounts.error()" searchPlaceholder="Search name or username" [rowClickable]="true"
                        emptyTitle="No accounts in view" emptyIcon="group" (search)="accounts.setSearch($event)" (pageChange)="accounts.setPage($event)"
                        (retry)="accounts.load()" (rowClick)="select($event)">
          <ng-template appCell="account_type" let-row>{{ row.account_type | label }}</ng-template>
          <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
          <ng-template appCell="balance" let-row><span class="num">{{ row.balance | arenaPoints }}</span></ng-template>
        </app-data-table>
      </mat-tab>

      <mat-tab label="Transactions">
        <app-data-table class="mt-4 block" [columns]="entryColumns" [rows]="entries.rows()" [total]="entries.total()" [page]="entries.page()" [pageSize]="entries.pageSize()"
                        [loading]="entries.loading()" [error]="entries.error()" [filters]="true" searchPlaceholder="Search account, reference or remarks"
                        emptyTitle="No balance activity" emptyIcon="receipt_long" (search)="entries.setSearch($event)" (pageChange)="entries.setPage($event)" (retry)="entries.load()">
          <mat-button-toggle-group filters [value]="kind()" (change)="setKind($event.value)" aria-label="Filter by operation type" hideSingleSelectionIndicator>
            <mat-button-toggle value="">All</mat-button-toggle>
            @for (k of kinds; track k) { <mat-button-toggle [value]="k">{{ entryLabel[k] }}</mat-button-toggle> }
          </mat-button-toggle-group>
          <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'MMM d, y, HH:mm' : 'UTC' }} UTC</ng-template>
          <ng-template appCell="kind" let-row>{{ entryText(row.kind) }}</ng-template>
          <ng-template appCell="delta" let-row><span class="num" [class.plus]="!row.delta.startsWith('-')" [class.minus]="row.delta.startsWith('-')">{{ signed(row.delta) }}</span></ng-template>
          <ng-template appCell="balance_after" let-row><span class="num">{{ row.balance_after | arenaPoints }}</span></ng-template>
          <ng-template appCell="status" let-row><app-status-chip [status]="row.operation_status ?? 'posted'" /></ng-template>
          <ng-template appCell="reference" let-row><span class="ref">{{ row.operation_reference ?? row.reference }}</span></ng-template>
          <ng-template appCell="remarks" let-row>{{ row.operation_remarks ?? row.memo ?? '—' }}</ng-template>
        </app-data-table>
      </mat-tab>

      @if (adminView()) {
        <mat-tab [label]="'Adjustments' + (summary()?.pending_corrections ? ' (' + summary()!.pending_corrections + ')' : '')">
          <app-data-table class="mt-4 block" [columns]="operationColumns" [rows]="operations.rows()" [total]="operations.total()" [page]="operations.page()"
                          [pageSize]="operations.pageSize()" [loading]="operations.loading()" [error]="operations.error()" [filters]="true"
                          searchPlaceholder="Search reference, remarks or player" emptyTitle="No balance adjustments yet" emptyIcon="rule"
                          (search)="operations.setSearch($event)" (pageChange)="operations.setPage($event)" (retry)="operations.load()">
            <mat-button-toggle-group filters [value]="opStatus()" (change)="setOpStatus($event.value)" aria-label="Filter by status" hideSingleSelectionIndicator>
              <mat-button-toggle value="">All</mat-button-toggle>
              <mat-button-toggle value="pending">Awaiting review</mat-button-toggle>
              <mat-button-toggle value="applied">Applied</mat-button-toggle>
              <mat-button-toggle value="rejected">Rejected</mat-button-toggle>
            </mat-button-toggle-group>
            <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'MMM d, y, HH:mm' : 'UTC' }} UTC</ng-template>
            <ng-template appCell="reference" let-row><span class="ref">{{ row.reference }}</span></ng-template>
            <ng-template appCell="kind" let-row>{{ operationText(row.kind) }}{{ row.direction ? ' (' + row.direction + ')' : '' }}</ng-template>
            <ng-template appCell="account" let-row>{{ row.target_username ?? (row.accounts_affected + ' players') }}</ng-template>
            <ng-template appCell="amount" let-row><span class="num">{{ row.status === 'applied' ? signed(row.total_delta) : points(row.amount) }}</span></ng-template>
            <ng-template appCell="status" let-row><app-status-chip [status]="row.status" [text]="row.status === 'pending' ? 'awaiting review' : null" /></ng-template>
            <ng-template appCell="remarks" let-row>{{ row.remarks ?? '—' }}{{ row.review_remarks ? ' · Review: ' + row.review_remarks : '' }}</ng-template>
            <ng-template appCell="actions" let-row>
              @if (row.status === 'pending' && canManage()) {
                @if (row.created_by_id !== userId()) {
                  <span class="row-actions">
                    <button mat-button type="button" [disabled]="busy()" (click)="approve(row)">Approve</button>
                    <button mat-button type="button" [disabled]="busy()" (click)="reject(row)">Reject</button>
                  </span>
                } @else { <span class="muted">Needs another admin</span> }
              }
            </ng-template>
          </app-data-table>
        </mat-tab>
      }
    </mat-tab-group>
  `,
})
export class BalanceManagementPage {
  private readonly api = inject(AdminApi);
  private readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  private readonly confirm = inject(ConfirmService);
  private readonly toast = inject(ToastService);
  private readonly destroy = inject(DestroyRef);

  readonly entryLabel = ENTRY_LABEL;
  readonly kinds = Object.keys(ENTRY_LABEL) as PlayEntryKind[];
  readonly roles = computed(() => (this.canManage() ? [...ROLE_FILTERS, LOADER_FILTER] : ROLE_FILTERS));
  readonly entryText = (kind: string): string => ENTRY_LABEL[kind as PlayEntryKind] ?? kind;
  readonly operationText = (kind: string): string => OPERATION_LABEL[kind as BalanceOperationKind] ?? kind;
  readonly userId = computed(() => this.auth.user()?.id ?? null);
  /** Hints only: the API decides again on every request. */
  readonly canManage = computed(() => this.summary()?.can_manage ?? false);
  readonly isLoader = computed(() => this.auth.user()?.account_type === 'loader');
  readonly allocation = signal<string | null>(null);
  readonly loaderTarget = computed(() => {
    const account = this.selected()?.account;
    return account && ['master_agent', 'agent', 'player'].includes(account.account_type) ? account : null;
  });
  readonly adminView = computed(() => this.auth.user()?.account_type === 'admin' && this.auth.hasPermission('points.view_all'));

  readonly accountColumns: Column<BalanceAccount>[] = [
    { key: 'username', label: 'Account' },
    { key: 'full_name', label: 'Name', hideOnMobile: true },
    { key: 'account_type', label: 'Role' },
    { key: 'parent_username', label: 'Assigned to', hideOnMobile: true, cell: (r) => r.parent_username ?? '—' },
    { key: 'status', label: 'Status', hideOnMobile: true },
    { key: 'balance', label: 'Available balance', align: 'end' },
  ];
  readonly entryColumns: Column<PlayWalletEntry>[] = [
    { key: 'created_at', label: 'When (UTC)' },
    { key: 'reference', label: 'Reference', hideOnMobile: true },
    { key: 'username', label: 'Account' },
    { key: 'kind', label: 'Operation' },
    { key: 'delta', label: 'Amount', align: 'end' },
    { key: 'balance_after', label: 'Balance after', align: 'end', hideOnMobile: true },
    { key: 'status', label: 'Status', hideOnMobile: true },
    { key: 'remarks', label: 'Remarks', hideOnMobile: true },
  ];
  readonly operationColumns: Column<BalanceOperation>[] = [
    { key: 'created_at', label: 'When (UTC)' },
    { key: 'reference', label: 'Reference' },
    { key: 'kind', label: 'Operation' },
    { key: 'account', label: 'Account', hideOnMobile: true },
    { key: 'amount', label: 'Amount', align: 'end' },
    { key: 'status', label: 'Status' },
    { key: 'remarks', label: 'Remarks', hideOnMobile: true },
    { key: 'actions', label: '', align: 'end' },
  ];

  readonly accounts = new PagedList<BalanceAccount>((q) => this.api.balanceAccounts(q));
  readonly entries = new PagedList<PlayWalletEntry>((q) => this.api.balanceEntries(q));
  readonly operations = new PagedList<BalanceOperation>((q) => this.api.balanceOperations(q));
  readonly summary = signal<BalanceSummary | null>(null);
  readonly selected = signal<BalanceAccountDetail | null>(null);
  readonly options = signal<BalanceAccount[]>([]);
  readonly correctable = signal<PlayWalletEntry[]>([]);
  readonly grants = signal<BalanceOperation[]>([]);
  readonly role = signal<AccountType | ''>('');
  readonly kind = signal<PlayEntryKind | ''>('');
  readonly opStatus = signal('');
  readonly from = signal('');
  readonly to = signal('');
  readonly busy = signal(false);
  tab = 0;

  readonly search = new FormControl<string | BalanceAccount>('', { nonNullable: true });
  readonly loaderGrantForm = new FormGroup({
    amount: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(AMOUNT)] }),
    reason: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.minLength(5), Validators.maxLength(300)] }),
  });
  readonly accountGrantForm = new FormGroup({
    amount: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(AMOUNT)] }),
    remarks: new FormControl('', { nonNullable: true, validators: [Validators.maxLength(300)] }),
  });
  readonly correctionForm = new FormGroup({
    basis: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    amount: new FormControl('', { nonNullable: true, validators: [Validators.pattern(AMOUNT)] }),
    remarks: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.minLength(5), Validators.maxLength(300)] }),
  });
  /** One key per intended operation: a retried submission of the same form replays instead of posting twice. */
  private accountGrantKey: string | null = null;
  private loaderGrantKey: string | null = null;
  private readonly loaderGrantDir = viewChild<FormGroupDirective>('loaderGrantDir');
  private readonly accountGrantDir = viewChild<FormGroupDirective>('accountGrantDir');
  private readonly correctionDir = viewChild<FormGroupDirective>('correctionDir');
  private correctionKey: string | null = null;

  constructor() {
    this.refresh();
    this.accountGrantForm.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => (this.accountGrantKey = null));
    this.loaderGrantForm.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => (this.loaderGrantKey = null));
    this.correctionForm.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => (this.correctionKey = null));
    this.search.valueChanges.pipe(
      startWith(''),
      debounceTime(250),
      distinctUntilChanged(),
      switchMap((q) => typeof q !== 'string' ? of(null) : this.api.balanceAccounts({ q, page_size: 20, account_type: this.role() || null }).pipe(catchError(() => of(null)))),
      takeUntilDestroyed(),
    ).subscribe((page) => { if (page) this.options.set(page.items); });
    inject(RealtimeService).on('betting.ledger_updated', 'connected', 'notification.created', 'balance.updated').pipe(debounceTime(300), takeUntilDestroyed()).subscribe(() => this.refresh());
  }

  refresh(): void {
    this.api.balanceSummary().pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: (s) => {
        this.summary.set(s);
        if (this.adminView()) this.operations.load();
      },
      error: (e) => this.toast.error(e),
    });
    if (this.isLoader()) {
      this.api.loaderAllocation().pipe(takeUntilDestroyed(this.destroy)).subscribe({
        next: (row) => this.allocation.set(row.balance),
        error: (e) => this.toast.error(e),
      });
    }
    this.accounts.load();
    this.entries.load();
    const id = this.selected()?.account.id;
    if (id) this.loadAccount(id);
  }

  display = (a: BalanceAccount | string | null): string => (typeof a === 'string' ? a : a ? `${a.username} — ${a.full_name}` : '');

  select(account: BalanceAccount): void {
    if (this.selected()?.account.id !== account.id) this.accountGrantKey = null;
    this.search.setValue(account, { emitEvent: false });
    this.entries.setFilter('user_id', account.id);
    this.loadAccount(account.id);
  }

  clearSelection(): void {
    this.accountGrantKey = null;
    this.selected.set(null);
    this.search.setValue('');
    this.entries.setFilter('user_id', null);
    this.accounts.setFilter('parent_id', null);
  }

  showDownline(account: BalanceAccount): void {
    this.accounts.setFilter('parent_id', account.id);
    this.tab = 0;
  }

  setRole(role: AccountType | ''): void {
    this.role.set(role);
    this.accounts.setFilter('account_type', role || null);
  }

  setKind(kind: PlayEntryKind | ''): void {
    this.kind.set(kind);
    this.entries.setFilter('kind', kind || null);
  }

  setOpStatus(status: string): void {
    this.opStatus.set(status);
    this.operations.setFilter('operation_status', status || null);
  }

  setDate(key: 'date_from' | 'date_to', value: string): void {
    (key === 'date_from' ? this.from : this.to).set(value);
    this.entries.setFilter(key, value || null);
    if (this.adminView()) this.operations.setFilter(key, value || null);
    const id = this.selected()?.account.id;
    if (id) this.loadAccount(id);
  }

  private loadAccount(id: string): void {
    this.api.balanceAccount(id, { date_from: this.from() || null, date_to: this.to() || null }).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: (detail) => {
        this.selected.set(detail);
        this.clear(this.correctionForm, this.correctionDir());
        if (this.canManage() && detail.account.account_type === 'player') this.loadCorrectionBases(id);
      },
      error: (e) => this.toast.error(e),
    });
  }

  private loadCorrectionBases(id: string): void {
    this.api.balanceEntries({ user_id: id, page_size: 100 }).subscribe({
      next: (page) => this.correctable.set(page.items.filter((e) => CORRECTABLE.includes(e.kind))),
      error: () => this.correctable.set([]),
    });
    this.api.balanceOperations({ kind: 'uniform_grant', operation_status: 'applied', page_size: 100 }).subscribe({
      next: (page) => this.grants.set(page.items),
      error: () => this.grants.set([]),
    });
  }

  subtitle(): string {
    if (this.canManage()) return 'Add balance to one player, with full history. Players start at 0.';
    if (this.isLoader()) return 'Add balance to a master agent, agent, or player from the allocation an administrator gave you.';
    return 'Balances and balance history of the accounts assigned to you (read-only).';
  }

  async loaderGrant(): Promise<void> {
    const target = this.loaderTarget();
    if (!target || target.status !== 'active' || this.loaderGrantForm.invalid) {
      this.loaderGrantForm.markAllAsTouched();
      return;
    }
    const { amount, reason } = this.loaderGrantForm.getRawValue();
    const ok = await this.confirm.ask({
      title: `Add balance to ${target.username}?`,
      message: `${target.username} (${target.account_type.replace(/_/g, ' ')}) currently has ${this.points(target.balance)}. Add ${this.points(amount)}. Your allocation is reduced by the same amount. Reason: ${reason.trim()}`,
      confirmText: 'Add balance',
    });
    if (ok === null) return;
    this.loaderGrantKey ??= newIdempotencyKey('loader-grant');
    this.run(this.api.loaderGrant({
      target_user_id: target.id, amount, reason: reason.trim(), idempotency_key: this.loaderGrantKey,
    }), (grant) => {
      this.toast.success(`${grant.reference}: ${this.points(grant.amount)} added to ${grant.recipient_username}.`);
      this.allocation.set(grant.loader_balance_after);
      this.clear(this.loaderGrantForm, this.loaderGrantDir());
      this.loaderGrantKey = null;
    });
  }

  async accountGrant(): Promise<void> {
    const target = this.selected()?.account;
    if (!target || !['player', 'agent', 'master_agent', 'loader'].includes(target.account_type) || this.accountGrantForm.invalid) {
      this.accountGrantForm.markAllAsTouched();
      return;
    }
    const { amount, remarks } = this.accountGrantForm.getRawValue();
    const ok = await this.confirm.ask({
      title: `Add balance to ${target.username}?`,
      message: `${target.username} receives ${this.points(amount)}. Every other account stays the same. This is recorded in their history and can be reversed only by a reviewed correction.`,
      confirmText: 'Add balance',
    });
    if (ok === null) return;
    this.accountGrantKey ??= newIdempotencyKey('account-grant');
    this.run(this.api.accountGrant({
      target_user_id: target.id, amount, remarks: remarks.trim() || null, idempotency_key: this.accountGrantKey,
    }), (op) => {
      this.toast.success(`${op.reference}: ${this.points(op.amount)} added to ${op.target_username ?? target.username}.`);
      this.clear(this.accountGrantForm, this.accountGrantDir());
      this.accountGrantKey = null;
    });
  }

  async reset(): Promise<void> {
    const s = this.summary();
    if (!s) return;
    const remarks = await this.confirm.ask({
      title: 'Reset every player?',
      message: `Every active player's balance becomes ${this.points(s.starting_balance)}. Balances above it go down. This is refused while any stake is open.`,
      confirmText: 'Reset balances',
      destructive: true,
      inputLabel: 'Remarks (optional)',
    });
    if (remarks === null) return;
    this.run(this.api.resetBalances({ remarks: remarks || null, idempotency_key: newIdempotencyKey('reset') }), (op) => {
      this.toast.success(`${op.reference}: ${op.accounts_affected} balances reset.`);
    });
  }

  async propose(): Promise<void> {
    const target = this.selected()?.account;
    if (!target || this.correctionForm.invalid) { this.correctionForm.markAllAsTouched(); return; }
    const { basis, amount, remarks } = this.correctionForm.getRawValue();
    const [type, id] = basis.split(':');
    const ok = await this.confirm.ask({
      title: 'Submit correction for review?',
      message: `A correction for ${target.username} is recorded as awaiting review. Nothing changes until a different administrator approves it.`,
      confirmText: 'Submit',
    });
    if (ok === null) return;
    this.correctionKey ??= newIdempotencyKey('correction');
    const body = type === 'entry'
      ? { target_user_id: target.id, entry_id: id, amount: amount || null, remarks, idempotency_key: this.correctionKey }
      : { target_user_id: target.id, operation_id: id, remarks, idempotency_key: this.correctionKey };
    this.run(this.api.proposeCorrection(body), (op) => {
      this.toast.success(`${op.reference} submitted for review.`);
      this.clear(this.correctionForm, this.correctionDir());
      this.correctionKey = null;
    });
  }

  async approve(op: BalanceOperation): Promise<void> {
    const remarks = await this.confirm.ask({
      title: `Approve ${op.reference}?`,
      message: `${op.direction === 'debit' ? 'Deducts' : 'Credits'} ${this.points(op.amount)} ${op.direction === 'debit' ? 'from' : 'to'} ${op.target_username}. Reason given: ${op.remarks}`,
      confirmText: 'Approve',
      inputLabel: 'Review remarks (optional)',
    });
    if (remarks === null) return;
    this.run(this.api.approveCorrection(op.id, remarks || null), (r) => this.toast.success(`${r.reference} applied.`));
  }

  async reject(op: BalanceOperation): Promise<void> {
    const remarks = await this.confirm.ask({
      title: `Reject ${op.reference}?`, message: 'The correction is closed without changing any balance.', confirmText: 'Reject',
      destructive: true, inputLabel: 'Reason', inputRequired: true, inputMinLength: 5,
    });
    if (remarks === null) return;
    this.run(this.api.rejectCorrection(op.id, remarks), (r) => this.toast.success(`${r.reference} rejected.`));
  }

  private run<T>(request: Observable<T>, done: (value: T) => void): void {
    this.busy.set(true);
    request.pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: (op) => { this.busy.set(false); done(op); this.refresh(); },
      error: (e) => { this.busy.set(false); this.toast.error(MESSAGES[toApiError(e).code] ?? e); },
    });
  }

  /** Resets values and the submitted state, so a finished form does not show "required" errors. */
  private clear(form: FormGroup, dir: FormGroupDirective | undefined): void {
    if (dir) dir.resetForm();
    else form.reset();
  }

  points(value: string | null | undefined): string {
    return formatArenaPoints(toCents(value ?? '0') ?? 0);
  }

  signed(delta: string): string {
    const cents = toCents(delta) ?? 0;
    return `${cents > 0 ? '+' : cents < 0 ? '−' : ''}${formatArenaPoints(Math.abs(cents))}`;
  }
}

/** Plain-language versions of the API's balance-management error codes. */
const MESSAGES: Record<string, string> = {
  insufficient_balance: 'Insufficient balance: the player no longer has enough to reverse this amount.',
  open_stakes: 'Balances cannot be reset while stakes are open. Settle or cancel them first.',
  reset_disabled: 'Resets are disabled by the reset policy.',
  second_reviewer_required: 'A different administrator must review this correction.',
  correction_exceeds_entry: 'That amount is more than what remains uncorrected on the transaction.',
  already_received: 'The player already received this grant, or a correction for it exists.',
  not_eligible: 'The player did not exist when this grant was made.',
  already_reviewed: 'This correction was already reviewed.',
  allocation_exceeded: 'That amount is more than the allocation an administrator assigned to you.',
  idempotency_conflict: 'This request was already submitted with different details. Reload and try again.',
  balance_limit: 'The operation would exceed the maximum balance.',
  no_participants: 'There are no active players.',
  not_active: 'Only an active player can receive a balance.',
};

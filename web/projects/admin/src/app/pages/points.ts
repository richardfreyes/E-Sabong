import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, input, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, FormGroupDirective, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatDialog } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatTabsModule } from '@angular/material/tabs';
import { Subject, Subscription, debounceTime, firstValueFrom, map, startWith } from 'rxjs';

import { CellDefDirective, Column, ConfirmService, DataTableComponent, FieldErrorComponent, PageHeaderComponent, StatCardComponent } from '@shared/components';
import type { AccountType, HierarchyNode, LedgerEntry, PointTransaction, PointsSummary, TenantProfile, User } from '@shared/models';
import { LabelPipe, PointsPipe } from '@shared/pipes';
import { AdminApi, AuthStore, PagedList, RealtimeService, ToastService, newIdempotencyKey, toApiError } from '@shared/services';
import { PointsHistoryComponent } from './points-history';
import { MANILA, TransactionDialogComponent, TransactionDialogData, partyLabel, typeLabel } from './transaction-dialog';
import { UserPickerComponent } from './user-picker';

type Op = 'issue' | 'transfer' | 'adjust';
type Wallet = 'self' | 'other';

const REASON = [Validators.required, Validators.minLength(3), Validators.maxLength(300), Validators.pattern(/\S.{2,}/)];

@Component({
  selector: 'app-admin-points',
  imports: [ReactiveFormsModule, DatePipe, LabelPipe, PointsPipe, MatTabsModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, MatButtonModule, MatButtonToggleModule, MatIconModule, PageHeaderComponent, StatCardComponent,
    DataTableComponent, CellDefDirective, FieldErrorComponent, UserPickerComponent, PointsHistoryComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './points.html',
  styleUrl: './points.scss',
})
export class PointsPage {
  private summaryRequest?: Subscription;
  private recentRequest?: Subscription;
  private readonly formDirective = viewChild(FormGroupDirective);
  readonly user = input<string | null>(null);
  private readonly api = inject(AdminApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly dialog = inject(MatDialog);
  private readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  private readonly destroy = inject(DestroyRef);
  readonly MANILA = MANILA;
  readonly me = this.auth.user;
  readonly isAdmin = computed(() => this.me()?.account_type === 'admin');
  readonly canViewAll = computed(() => this.auth.hasPermission('points.view_all'));
  readonly canRefund = computed(() => this.isAdmin() && this.auth.hasPermission('points.adjust') && this.auth.hasPermission('points.transfer'));
  readonly summary = signal<PointsSummary | null>(null);
  readonly summaryError = signal<string | null>(null);
  readonly recent = signal<LedgerEntry[] | null>(null);
  readonly recentError = signal<string | null>(null);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly receipt = signal<PointTransaction | null>(null);
  readonly historyRefresh = signal(0);
  readonly recipient = signal<User | null>(null);
  readonly senderAccount = signal<User | null>(null);
  readonly recipientType = signal<AccountType | null>(null);
  readonly accountType = signal('');
  readonly roles: AccountType[] = ['admin', 'master_agent', 'agent', 'player', 'declarator', 'operator'];
  readonly opLabel: Record<Op, string> = { issue: 'Create Points', transfer: 'Transfer Points', adjust: 'Adjust' };
  /** Only Admins create or move points; the API enforces the same rule. */
  readonly ops = computed<Op[]>(() => (['issue', 'transfer', 'adjust'] as Op[]).filter(op =>
    this.isAdmin() && this.auth.hasPermission(op === 'transfer' ? 'points.transfer' : op === 'issue' ? 'points.allocate' : 'points.adjust')));
  readonly op = signal<Op>(this.ops()[0] ?? 'transfer');
  private idempotencyKey = newIdempotencyKey('points');
  private retryingUncertainPost = false;
  readonly form = inject(FormBuilder).group({
    target: ['self' as Wallet],
    sender: ['self' as Wallet],
    from_user_id: [null as string | null],
    user_id: [null as string | null],
    amount: [null as number | null, [Validators.required, Validators.min(1), Validators.max(1_000_000_000), Validators.pattern(/^\d+$/)]],
    memo: [''],
    direction: ['credit' as 'credit' | 'debit'],
  });
  private readonly value = toSignal(this.form.valueChanges.pipe(startWith(null), map(() => this.form.getRawValue())), { requireSync: true });
  readonly ownBalance = computed(() => this.summary()?.own_balance ?? null);
  /** Balance the operation draws from, as last read from the server. */
  readonly senderBalance = computed(() => this.value().sender === 'self' ? this.ownBalance() : this.senderAccount()?.balance ?? null);
  readonly targetBalance = computed(() => this.op() === 'issue' && this.value().target === 'self' ? this.ownBalance() : this.recipient()?.balance ?? null);
  readonly needsAccount = computed(() => this.op() !== 'issue' || this.value().target === 'other');
  readonly accounts = new PagedList<User>(q => this.api.users(q));
  readonly accountColumns: Column<User>[] = [
    { key: 'username', label: 'Account', sortable: true },
    { key: 'full_name', label: 'Name', hideOnMobile: true },
    { key: 'account_type', label: 'Role' },
    { key: 'balance', label: 'Points', align: 'end' },
    { key: 'id', label: 'Account ID', hideOnMobile: true },
  ];
  readonly assigned = signal<HierarchyNode[]>([]);
  readonly assignedLoading = signal(true);
  readonly assignedError = signal<string | null>(null);
  readonly assignedTitle = computed(() => this.me()?.account_type === 'master_agent' ? 'Your agents' : 'Your players');
  readonly assignedColumns: Column<HierarchyNode>[] = [
    { key: 'username', label: 'Account' }, { key: 'full_name', label: 'Name', hideOnMobile: true },
    { key: 'account_type', label: 'Role' }, { key: 'balance', label: 'Points', align: 'end' },
    { key: 'child_count', label: 'Assigned to them', align: 'end', hideOnMobile: true },
  ];
  readonly typeLabel = typeLabel;
  readonly partyLabel = partyLabel;

  constructor() {
    this.setValidators();
    this.refreshAll();
    const refresh = new Subject<void>();
    refresh.pipe(debounceTime(150), takeUntilDestroyed(this.destroy)).subscribe(() => this.refreshAll());
    inject(RealtimeService).on('points.updated', 'connected').pipe(takeUntilDestroyed(this.destroy)).subscribe(() => refresh.next());
    this.form.valueChanges.pipe(takeUntilDestroyed(this.destroy)).subscribe(() => {
      this.error.set(null); this.idempotencyKey = newIdempotencyKey('points'); this.retryingUncertainPost = false;
    });
    for (const control of [this.form.controls.target, this.form.controls.sender]) {
      control.valueChanges.pipe(takeUntilDestroyed(this.destroy)).subscribe(() => queueMicrotask(() => this.setValidators()));
    }
    queueMicrotask(() => {
      if (this.destroy.destroyed || !this.user()) return;
      if (this.ops().includes('transfer')) this.op.set('transfer');
      this.setValidators();
      this.form.patchValue({ user_id: this.user() });
    });
  }

  setOp(op: Op): void {
    if (this.busy() || !this.ops().includes(op)) return;
    this.op.set(op);
    this.idempotencyKey = newIdempotencyKey('points');
    this.retryingUncertainPost = false;
    this.error.set(null);
    this.setValidators();
  }

  private setValidators(): void {
    const { user_id, from_user_id, memo } = this.form.controls;
    user_id.setValidators(this.needsAccount() ? [Validators.required] : []);
    from_user_id.setValidators(this.op() === 'transfer' && this.form.controls.sender.value === 'other' ? [Validators.required] : []);
    memo.setValidators(this.op() === 'transfer' ? [Validators.maxLength(300)] : REASON);
    for (const control of [user_id, from_user_id, memo]) control.updateValueAndValidity({ emitEvent: false });
  }

  setAccountType(role: string): void { this.accountType.set(role); this.accounts.setFilter('account_type', role || null); }
  setRecipientType(role: AccountType | ''): void { this.recipientType.set(role || null); this.form.controls.user_id.reset(); }

  refreshAll(): void {
    this.loadSummary(); this.loadRecent(); this.loadAccounts(); this.historyRefresh.update(n => n + 1);
  }
  private loadAccounts(): void { if (this.canViewAll()) this.accounts.load(); else this.loadAssigned(); }
  loadAssigned(): void {
    this.api.hierarchy(null).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: rows => { this.assigned.set(rows); this.assignedLoading.set(false); this.assignedError.set(null); },
      error: error => { this.assignedLoading.set(false); this.assignedError.set(toApiError(error).message); },
    });
  }
  loadSummary(): void {
    this.summaryRequest?.unsubscribe();
    this.summaryRequest = this.api.pointsSummary().pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: summary => { this.summary.set(summary); this.summaryError.set(null); },
      error: error => this.summaryError.set(toApiError(error).message),
    });
  }
  loadRecent(): void {
    const id = this.me()?.id;
    if (!id) return;
    this.recentRequest?.unsubscribe();
    this.recentRequest = this.api.accountEntries(id, { page_size: 6 }).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: page => { this.recent.set(page.items); this.recentError.set(null); },
      error: error => { this.recent.set([]); this.recentError.set(toApiError(error).message); },
    });
  }

  openReceipt(tx: PointTransaction): void {
    this.dialog.open<TransactionDialogComponent, TransactionDialogData>(TransactionDialogComponent, {
      data: { tx, canRefund: false }, width: '640px', maxWidth: '96vw',
    });
  }

  /** Client checks only prevent obvious mistakes; the server re-validates everything under lock. */
  private precheck(amount: number): string | null {
    const value = this.form.getRawValue();
    const op = this.op();
    if (op === 'transfer') {
      const from = value.sender === 'self' ? this.me()?.id : value.from_user_id;
      if (from && from === value.user_id) return 'Sender and recipient must be different wallets.';
      const balance = this.senderBalance();
      if (!this.retryingUncertainPost && balance !== null && amount > balance) {
        return `Insufficient points: the sender holds ${balance.toLocaleString()}. Create points or choose another sender.`;
      }
    }
    if (op === 'adjust' && value.direction === 'debit') {
      const balance = this.recipient()?.balance;
      if (!this.retryingUncertainPost && balance != null && amount > balance) return `Insufficient points: the account holds ${balance.toLocaleString()}.`;
    }
    return null;
  }

  private describe(amount: number, memo: string | null): string {
    const value = this.form.getRawValue();
    const target = this.recipient();
    const who = (user: User | null, id: string | null) => user ? `${user.username} (${user.account_type.replace('_', ' ')}, ID ${user.id})` : id ?? 'account';
    const n = amount.toLocaleString();
    switch (this.op()) {
      case 'issue':
        return value.target === 'self'
          ? `Create ${n} new points in your wallet? Issued supply increases by ${n}. Reason: ${memo}`
          : `Create ${n} new points and credit them directly to ${who(target, value.user_id)}? Issued supply increases by ${n}. Reason: ${memo}`;
      case 'transfer': {
        const sender = value.sender === 'self' ? `your wallet (${this.me()?.username})` : who(this.senderAccount(), value.from_user_id);
        return `Transfer ${n} existing points from ${sender} to ${who(target, value.user_id)}? The sender's balance decreases by ${n}; no points are created.${memo ? ` Remarks: ${memo}` : ''}`;
      }
      case 'adjust':
        return `${value.direction === 'credit' ? 'Credit' : 'Debit'} ${n} points ${value.direction === 'credit' ? 'to' : 'from'} ${who(target, value.user_id)}? This is an audited correction. Reason: ${memo}`;
    }
  }

  async submit(): Promise<void> {
    if (this.busy()) return;
    const value = this.form.getRawValue();
    const memo = value.memo?.trim() || null;
    if (this.op() !== 'transfer' && (!memo || memo.length < 3)) this.form.controls.memo.setErrors({ minlength: { requiredLength: 3 } });
    if (this.form.invalid) { this.form.markAllAsTouched(); return; }
    const amount = Number(value.amount);
    if (!Number.isSafeInteger(amount) || amount < 1) { this.error.set('Enter a whole number of points.'); return; }
    const problem = this.precheck(amount);
    if (problem) { this.error.set(problem); return; }
    const op = this.op();
    const key = this.idempotencyKey;
    this.busy.set(true);
    this.form.disable({ emitEvent: false });
    this.error.set(null);
    try {
      const confirmText = op === 'issue' ? 'Create points' : op === 'transfer' ? 'Confirm transfer' : 'Confirm adjustment';
      if (await this.confirm.ask({ title: this.opLabel[op], message: this.describe(amount, memo), confirmText }) === null) return;
      if (this.destroy.destroyed) return;
      const request = op === 'issue'
        ? this.api.allocate({ user_id: value.target === 'self' ? this.me()!.id : value.user_id!, amount, memo }, key)
        : op === 'transfer'
          ? this.api.transfer({ to_user_id: value.user_id!, ...(value.sender === 'other' ? { from_user_id: value.from_user_id } : {}), amount, memo }, key)
          : this.api.adjust({ user_id: value.user_id!, amount, direction: value.direction!, reason: memo! }, key);
      const tx = await firstValueFrom(request.pipe(takeUntilDestroyed(this.destroy)));
      this.retryingUncertainPost = false;
      this.receipt.set(tx);
      this.toast.success(`${typeLabel(tx)} completed · ${tx.amount.toLocaleString()} points → ${partyLabel(tx.destination)}`);
      this.idempotencyKey = newIdempotencyKey('points');
      const reset = { target: value.target, sender: value.sender, from_user_id: null, user_id: null, amount: null, memo: '', direction: 'credit' as const };
      const directive = this.formDirective();
      if (directive) directive.resetForm(reset);
      else this.form.reset(reset, { emitEvent: false });
      this.recipient.set(null);
      this.senderAccount.set(null);
      this.refreshAll();
    } catch (error) {
      if (this.destroy.destroyed) return;
      const failure = toApiError(error);
      this.retryingUncertainPost = failure.status === 0 || failure.status >= 500;
      if (failure.status >= 400 && failure.status < 500 && failure.code !== 'idempotency_conflict') this.idempotencyKey = newIdempotencyKey('points');
      this.error.set(this.retryingUncertainPost ? `${failure.message} Keep this form unchanged and retry safely; the same request cannot post twice.` : failure.message);
    } finally {
      if (!this.destroy.destroyed) { this.busy.set(false); this.form.enable({ emitEvent: false }); }
    }
  }
}

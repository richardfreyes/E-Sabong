import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatTabsModule } from '@angular/material/tabs';
import { Observable } from 'rxjs';

import { CellDefDirective, Column, DataTableComponent, FieldErrorComponent, PageHeaderComponent, StatCardComponent } from '@shared/components';
import type { PointTransaction, PointsSummary, TenantProfile } from '@shared/models';
import { LabelPipe, PointsPipe } from '@shared/pipes';
import { AdminApi, AuthStore, PagedList, ToastService, newIdempotencyKey, toApiError } from '@shared/services';
import { UserPickerComponent } from './user-picker';

type Op = 'transfer' | 'allocate' | 'adjust';

@Component({
  selector: 'app-admin-points',
  imports: [
    ReactiveFormsModule, DatePipe, LabelPipe, PointsPipe, MatTabsModule, MatFormFieldModule, MatInputModule, MatButtonModule, MatButtonToggleModule,
    MatIconModule, PageHeaderComponent, StatCardComponent, DataTableComponent, CellDefDirective, FieldErrorComponent, UserPickerComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Demonstration points" subtitle="A closed-loop practice balance. Points have no monetary value and are not tied to match results." />
    <p class="m-0 mb-4 flex items-start gap-2 rounded-xl bg-secondary-container p-3 text-sm text-on-secondary-container" role="note">
      <mat-icon aria-hidden="true">info</mat-icon>{{ summary()?.disclaimer ?? 'Demonstration points have no monetary value and cannot be redeemed.' }}
    </p>

    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      @if (summary()?.scope === 'tenant') {
        <app-stat-card label="Total issued" icon="add_circle" [value]="summary()!.total_issued | points" />
        <app-stat-card label="In circulation" icon="toll" [value]="summary()!.circulating | points" />
      } @else {
        <app-stat-card label="Your balance" icon="account_balance_wallet" [loading]="!summary()" [value]="summary()?.own_balance | points" />
        <app-stat-card label="Held by your downline" icon="groups" [loading]="!summary()" [value]="summary()?.circulating | points" />
      }
      <app-stat-card label="Accounts" icon="group" [loading]="!summary()" [value]="summary()?.accounts ?? null" />
      <app-stat-card label="Transactions, 30 days" icon="swap_horiz" [loading]="!summary()" [value]="txCount()" />
    </div>

    @if (ops().length) {
      <section class="mt-6 rounded-2xl border border-outline-variant p-4">
        <mat-tab-group mat-stretch-tabs="false" animationDuration="0ms" [selectedIndex]="0" (selectedIndexChange)="setOp(ops()[$event])">
          @for (op of ops(); track op) { <mat-tab [label]="opLabel[op]" /> }
        </mat-tab-group>
        <form [formGroup]="form" (ngSubmit)="submit()" class="mt-4 grid grid-cols-1 gap-x-3 md:grid-cols-[2fr_1fr_2fr_auto]" novalidate>
          <app-user-picker formControlName="user_id" [label]="op() === 'transfer' ? 'Recipient (in your downline)' : 'Account'" [excludeId]="me()?.id ?? null" />
          <mat-form-field><mat-label>Amount</mat-label><input matInput type="number" min="1" step="1" formControlName="amount" />
            <mat-error><app-field-error [control]="form.controls.amount" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>{{ op() === 'adjust' ? 'Reason (required)' : 'Memo (optional)' }}</mat-label><input matInput formControlName="memo" />
            <mat-error><app-field-error [control]="form.controls.memo" /></mat-error></mat-form-field>
          <div class="flex items-start gap-2 md:pt-2">
            @if (op() === 'adjust') {
              <mat-button-toggle-group formControlName="direction" aria-label="Direction">
                <mat-button-toggle value="credit">Credit</mat-button-toggle><mat-button-toggle value="debit">Debit</mat-button-toggle>
              </mat-button-toggle-group>
            }
            <button mat-flat-button type="submit" [disabled]="busy()" [attr.aria-busy]="busy()">{{ busy() ? 'Posting…' : opLabel[op()] }}</button>
          </div>
          @if (error()) { <p class="m-0 text-sm text-error md:col-span-4" role="alert">{{ error() }}</p> }
        </form>
      </section>
    }

    <section class="mt-6">
      <h2 class="m-0 mb-3 text-base font-semibold">Transactions</h2>
      <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                      [loading]="list.loading()" [error]="list.error()" [filters]="true" searchPlaceholder="Search memo" emptyTitle="No transactions yet"
                      emptyIcon="swap_horiz" (search)="list.setSearch($event)" (pageChange)="list.setPage($event)" (retry)="list.load()">
        <mat-button-toggle-group filters [value]="kind()" (change)="setKind($event.value)" aria-label="Filter by type" hideSingleSelectionIndicator>
          <mat-button-toggle value="">All</mat-button-toggle><mat-button-toggle value="allocation">Allocations</mat-button-toggle>
          <mat-button-toggle value="transfer">Transfers</mat-button-toggle><mat-button-toggle value="adjustment">Adjustments</mat-button-toggle>
        </mat-button-toggle-group>
        <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'medium' }}</ng-template>
        <ng-template appCell="kind" let-row>{{ row.kind | label }}</ng-template>
        <ng-template appCell="amount" let-row>{{ row.amount | points }}</ng-template>
      </app-data-table>
    </section>
  `,
})
export class PointsPage {
  readonly user = input<string | null>(null);
  private readonly api = inject(AdminApi);
  private readonly toast = inject(ToastService);
  private readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly me = this.auth.user;
  readonly summary = signal<PointsSummary | null>(null);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly kind = signal('');
  readonly opLabel: Record<Op, string> = { transfer: 'Transfer', allocate: 'Allocate', adjust: 'Adjust' };
  readonly ops = computed<Op[]>(() =>
    (['transfer', 'allocate', 'adjust'] as Op[]).filter((o) =>
      this.auth.hasPermission(o === 'transfer' ? 'points.transfer' : o === 'allocate' ? 'points.allocate' : 'points.adjust'),
    ),
  );
  readonly op = signal<Op>(this.ops()[0] ?? 'transfer');
  readonly txCount = computed(() => {
    const t = this.summary()?.transactions_30d;
    return t ? Object.values(t).reduce((s, n) => s + (n ?? 0), 0) : null;
  });
  /** Reused while the form is unchanged, so a retried submit cannot post twice. */
  private idempotencyKey = newIdempotencyKey('web');
  readonly form = inject(FormBuilder).group({
    user_id: [null as string | null, Validators.required],
    amount: [null as number | null, [Validators.required, Validators.min(1), Validators.max(1_000_000_000)]],
    memo: ['', Validators.maxLength(300)],
    direction: ['credit' as 'credit' | 'debit'],
  });
  readonly list = new PagedList<PointTransaction>((q) => this.api.transactions(q));
  readonly columns: Column<PointTransaction>[] = [
    { key: 'created_at', label: 'When' },
    { key: 'kind', label: 'Type' },
    { key: 'from', label: 'From', cell: (t) => t.source.username ?? 'Issuance' },
    { key: 'to', label: 'To', cell: (t) => t.destination.username ?? 'Issuance' },
    { key: 'amount', label: 'Amount', align: 'end' },
    { key: 'memo', label: 'Memo', hideOnMobile: true },
    { key: 'created_by_username', label: 'By', hideOnMobile: true },
  ];

  constructor() {
    this.loadSummary();
    this.list.load();
    // Any edit makes it a different operation, so it gets a fresh key.
    this.form.valueChanges.subscribe(() => {
      this.error.set(null);
      this.idempotencyKey = newIdempotencyKey('web');
    });
    queueMicrotask(() => this.user() && this.form.patchValue({ user_id: this.user() }));
  }

  setOp(op: Op): void {
    this.op.set(op);
    this.idempotencyKey = newIdempotencyKey('web');
    const memo = this.form.controls.memo;
    memo.setValidators(op === 'adjust' ? [Validators.required, Validators.minLength(3), Validators.maxLength(300)] : [Validators.maxLength(300)]);
    memo.updateValueAndValidity();
  }

  setKind(v: string): void {
    this.kind.set(v);
    this.list.setFilter('kind', v || null);
  }

  loadSummary(): void {
    this.api.pointsSummary().subscribe({ next: (s) => this.summary.set(s), error: () => undefined });
  }

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    const amount = Number(v.amount);
    const memo = v.memo?.trim() || null;
    const key = this.idempotencyKey;
    let req: Observable<PointTransaction>;
    switch (this.op()) {
      case 'transfer':
        req = this.api.transfer({ to_user_id: v.user_id!, amount, memo }, key);
        break;
      case 'allocate':
        req = this.api.allocate({ user_id: v.user_id!, amount, memo }, key);
        break;
      default:
        req = this.api.adjust({ user_id: v.user_id!, amount, direction: v.direction!, reason: memo ?? '' }, key);
    }
    this.busy.set(true);
    req.subscribe({
      next: (tx) => {
        this.busy.set(false);
        this.idempotencyKey = newIdempotencyKey('web');
        this.toast.success(`${tx.amount.toLocaleString()} points → ${tx.destination.username ?? 'issuance'}`);
        this.form.reset({ user_id: null, amount: null, memo: '', direction: 'credit' });
        this.loadSummary();
        this.list.load();
      },
      error: (e) => {
        this.busy.set(false);
        const err = toApiError(e);
        // Business rejections will not succeed on retry; let the next attempt be a new operation.
        if (err.status >= 400 && err.status < 500 && err.code !== 'idempotency_conflict') this.idempotencyKey = newIdempotencyKey('web');
        this.error.set(err.message);
      },
    });
  }
}

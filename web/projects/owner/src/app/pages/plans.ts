import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import { CellDefDirective, Column, DataTableComponent, FieldErrorComponent, PageHeaderComponent, StatusChipComponent } from '@shared/components';
import type { ModuleKey, Plan } from '@shared/models';
import { CURRENCIES, DEFAULT_CURRENCY, MoneyPipe, currencySymbol } from '@shared/pipes';
import { PlatformApi, ToastService, toApiError } from '@shared/services';

const MODULES: ModuleKey[] = ['events', 'matches', 'points', 'reports', 'notifications'];
const REVIEW_TEXT: Record<string, string> = { legacy_currency: 'legacy currency', prices_unapproved: 'prices not approved' };

@Component({
  selector: 'app-plan-dialog',
  imports: [ReactiveFormsModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatSelectModule, MatCheckboxModule, MatButtonModule, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ legacyCurrency ? 'Create PHP replacement for ' + plan!.name : plan ? 'Edit ' + plan.name : 'New plan' }}</h2>
    <mat-dialog-content>
      <form [formGroup]="form" id="plan-form" (ngSubmit)="save()" class="grid grid-cols-1 gap-x-3 sm:grid-cols-2" novalidate>
        <mat-form-field><mat-label>Code</mat-label><input matInput formControlName="code" [readonly]="!!plan && !legacyCurrency" />
          <mat-error><app-field-error [control]="form.controls.code" /></mat-error></mat-form-field>
        <mat-form-field><mat-label>Name</mat-label><input matInput formControlName="name" />
          <mat-error><app-field-error [control]="form.controls.name" /></mat-error></mat-form-field>
        <mat-form-field class="sm:col-span-2"><mat-label>Description</mat-label><input matInput formControlName="description" /></mat-form-field>
        <mat-form-field><mat-label>Kind</mat-label><mat-select formControlName="kind">
          <mat-option value="saas">SaaS subscription</mat-option><mat-option value="maintenance">Maintenance (licensed)</mat-option></mat-select></mat-form-field>
        <mat-form-field><mat-label>Currency</mat-label><input matInput [value]="currencyLabel" readonly data-field="currency" />
          <mat-hint>All new billing is in Philippine Peso</mat-hint></mat-form-field>
        <mat-form-field><mat-label>Monthly price</mat-label><span matTextPrefix>{{ symbol }}&nbsp;</span><input matInput type="number" min="0" step="0.01" formControlName="monthly" />
          <mat-error><app-field-error [control]="form.controls.monthly" /></mat-error></mat-form-field>
        <mat-form-field><mat-label>Annual price</mat-label><span matTextPrefix>{{ symbol }}&nbsp;</span><input matInput type="number" min="0" step="0.01" formControlName="annual" />
          <mat-error><app-field-error [control]="form.controls.annual" /></mat-error></mat-form-field>
        <mat-form-field><mat-label>Trial days</mat-label><input matInput type="number" min="0" formControlName="trial_days" /></mat-form-field>
        <mat-form-field><mat-label>Sort order</mat-label><input matInput type="number" formControlName="sort_order" /></mat-form-field>
        <mat-form-field><mat-label>Max accounts</mat-label><input matInput type="number" min="1" formControlName="max_users" /><mat-hint>Empty = unlimited</mat-hint></mat-form-field>
        <mat-form-field><mat-label>Max events / month</mat-label><input matInput type="number" min="1" formControlName="max_events_per_month" /><mat-hint>Empty = unlimited</mat-hint></mat-form-field>
        <mat-form-field class="sm:col-span-2"><mat-label>Included modules</mat-label>
          <mat-select formControlName="modules" multiple>@for (m of modules; track m) { <mat-option [value]="m">{{ m }}</mat-option> }</mat-select></mat-form-field>
        <mat-checkbox formControlName="is_active">Available for new subscriptions</mat-checkbox>
        <div class="sm:col-span-2" data-field="approval">
          @if (legacyCurrency) {
            <p class="m-0 text-sm" role="note">Enter new PHP prices for this replacement. The original {{ plan!.currency }} plan and historical invoices remain unchanged; no exchange rate is assumed.</p>
          }
          <mat-checkbox formControlName="approve_prices">These PHP prices are approved for sale</mat-checkbox>
          <p class="m-0 text-xs text-on-surface-variant">{{ approvalHint() }}</p>
        </div>
        @if (error()) { <p class="m-0 text-sm text-error sm:col-span-2" role="alert">{{ error() }}</p> }
      </form>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close>Cancel</button>
      <button mat-flat-button type="submit" form="plan-form" [disabled]="saving()" [attr.aria-busy]="saving()">Save plan</button>
    </mat-dialog-actions>
  `,
})
export class PlanDialog {
  readonly saving = signal(false);
  readonly plan = inject<Plan | null>(MAT_DIALOG_DATA);
  private readonly api = inject(PlatformApi);
  private readonly ref = inject(MatDialogRef<PlanDialog>);
  readonly modules = MODULES;
  // Billing is PHP only. A legacy plan created in another currency keeps its own code, so its
  // historical amounts are never relabelled as pesos.
  readonly currency = DEFAULT_CURRENCY;
  readonly legacyCurrency = !!this.plan && this.plan.currency !== DEFAULT_CURRENCY;
  readonly currencyLabel = CURRENCIES[this.currency] ? `${this.currency} · ${CURRENCIES[this.currency].name}` : this.currency;
  readonly symbol = currencySymbol(this.currency);
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).group({
    code: [this.legacyCurrency ? this.plan!.code.slice(0,44) + '-php' : this.plan?.code ?? '', [Validators.required, Validators.pattern(/^[a-z0-9_-]+$/)]],
    name: [this.plan?.name ?? '', Validators.required],
    description: [this.plan?.description ?? ''],
    kind: [this.plan?.kind ?? 'saas'],
    // New plans start without a price: the owner enters a real amount rather than accepting a default.
    monthly: [this.plan && !this.legacyCurrency ? this.plan.price_monthly_cents / 100 : (null as number | null), [Validators.required, Validators.min(0)]],
    annual: [this.plan && !this.legacyCurrency ? this.plan.price_annual_cents / 100 : (null as number | null), [Validators.required, Validators.min(0)]],
    trial_days: [this.plan?.trial_days ?? 14, [Validators.min(0)]],
    sort_order: [this.plan?.sort_order ?? 0],
    max_users: [(this.plan?.limits['max_users'] as number | null) ?? null as number | null],
    max_events_per_month: [(this.plan?.limits['max_events_per_month'] as number | null) ?? null as number | null],
    modules: [this.plan?.modules ?? [...MODULES]],
    is_active: [this.plan?.is_active ?? true],
    approve_prices: [false],
  });
  private readonly approvedPrices = !this.legacyCurrency && this.plan?.prices_approved_at
    ? { monthly: this.plan.price_monthly_cents, annual: this.plan.price_annual_cents }
    : null;

  /** Approval is tied to the exact amounts: a changed price needs a fresh approval. */
  approvalHint(): string {
    const v = this.form.getRawValue();
    const same = this.approvedPrices && Math.round(Number(v.monthly) * 100) === this.approvedPrices.monthly
      && Math.round(Number(v.annual) * 100) === this.approvedPrices.annual;
    if (same) return 'Current prices are approved. Tick again only to re-confirm.';
    return this.approvedPrices
      ? 'Prices changed: without approval this plan stops being sold until someone approves them.'
      : 'Not approved yet: the plan cannot be sold until its prices are approved.';
  }

  save(): void {
    if (this.saving()) return;
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    this.saving.set(true);
    this.error.set(null);
    const limits: Record<string, number> = {};
    if (v.max_users) limits['max_users'] = Number(v.max_users);
    if (v.max_events_per_month) limits['max_events_per_month'] = Number(v.max_events_per_month);
    const body = {
      name: v.name!, description: v.description || null, price_monthly_cents: Math.round(Number(v.monthly) * 100),
      price_annual_cents: Math.round(Number(v.annual) * 100), trial_days: Number(v.trial_days), sort_order: Number(v.sort_order),
      limits, modules: v.modules as ModuleKey[], is_active: !!v.is_active, approve_prices: !!v.approve_prices,
    };
    const req = this.plan && !this.legacyCurrency
      ? this.api.updatePlan(this.plan.id, body)
      : this.api.createPlan({ ...body, code: v.code!, kind: v.kind as Plan['kind'], currency: DEFAULT_CURRENCY });
    req.subscribe({ next: (p) => this.ref.close(p), error: (e) => { this.saving.set(false); this.error.set(toApiError(e).message); } });
  }
}

@Component({
  selector: 'app-owner-plans',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, StatusChipComponent, MatButtonModule, MatIconModule, MoneyPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Plans" subtitle="Software subscription plans and maintenance plans for licensed installs.">
      <button mat-flat-button type="button" (click)="edit(null)"><mat-icon>add</mat-icon>New plan</button>
    </app-page-header>
    @if (needsReview().length) {
      <p class="mb-4 rounded-lg border border-outline-variant p-3 text-sm" role="status" data-field="review-banner">
        <strong>{{ needsReview().length }} {{ needsReview().length === 1 ? 'plan needs review before it' : 'plans need review before they' }}</strong> can be sold:
        {{ reviewSummary() }}. Existing subscriptions are not affected.
      </p>
    }
    <app-data-table [columns]="columns" [rows]="plans()" [total]="plans().length" [loading]="loading()" [error]="error()" [searchable]="false"
                    [paginated]="false" [rowClickable]="true" emptyTitle="No plans" emptyIcon="sell" (rowClick)="edit($event)" (retry)="load()">
      <ng-template appCell="price" let-row>@if (row.currency === 'PHP') { {{ row.price_monthly_cents | money: row.currency }} / {{ row.price_annual_cents | money: row.currency }} } @else { Set PHP replacement prices }</ng-template>
      <ng-template appCell="limits" let-row>{{ row.limits.max_users ?? '∞' }} accounts · {{ row.limits.max_events_per_month ?? '∞' }} events/mo</ng-template>
      <ng-template appCell="is_active" let-row><app-status-chip [status]="saleStatus(row).status" [text]="saleStatus(row).text" /></ng-template>
    </app-data-table>
  `,
})
export class PlansPage {
  private readonly api = inject(PlatformApi);
  private readonly dialog = inject(MatDialog);
  private readonly toast = inject(ToastService);
  readonly plans = signal<Plan[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly columns: Column<Plan>[] = [
    { key: 'name', label: 'Plan' },
    { key: 'kind', label: 'Kind', hideOnMobile: true },
    { key: 'price', label: 'Monthly / annual', align: 'end' },
    { key: 'trial_days', label: 'Trial', align: 'end', hideOnMobile: true, cell: (p) => `${p.trial_days} d` },
    { key: 'limits', label: 'Limits', hideOnMobile: true },
    { key: 'is_active', label: 'Sale status' },
  ];
  readonly needsReview = computed(() => this.plans().filter((p) => p.is_active && !p.checkout_ready));
  readonly reviewSummary = computed(() => this.needsReview()
    .map((p) => `${p.name} (${(p.review_reasons ?? []).filter((r) => r !== 'inactive').map((r) => REVIEW_TEXT[r] ?? r).join(', ')})`).join('; '));

  saleStatus(p: Plan): { status: string; text: string } {
    if (!p.is_active) return { status: 'cancelled', text: 'retired' };
    if (p.review_reasons?.includes('legacy_currency')) return { status: 'under_review', text: `legacy ${p.currency} · review` };
    if (!p.checkout_ready) return { status: 'pending_approval', text: 'needs price approval' };
    return { status: 'active', text: 'for sale' };
  }

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.plans().subscribe({
      next: (p) => (this.plans.set(p), this.loading.set(false)),
      error: (e) => (this.error.set(toApiError(e).message), this.loading.set(false)),
    });
  }

  edit(plan: Plan | null): void {
    this.dialog.open(PlanDialog, { data: plan, width: '640px', maxWidth: '95vw' }).afterClosed().subscribe((p?: Plan) => {
      if (p) {
        this.toast.success(`${p.name} saved`);
        this.load();
      }
    });
  }
}

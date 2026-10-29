import { DEFAULT_BRAND_COLORS } from '@shared/services';
import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatMenuModule } from '@angular/material/menu';
import { MatSelectModule } from '@angular/material/select';
import { MatTabsModule } from '@angular/material/tabs';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Router, RouterLink } from '@angular/router';

import {
  BarChartComponent,
  CellDefDirective,
  Column,
  ConfirmService,
  DataTableComponent,
  EmptyStateComponent,
  ErrorStateComponent,
  FieldErrorComponent,
  LoadingComponent,
  PageHeaderComponent,
  StatusChipComponent,
} from '@shared/components';
import type { AuditEntry, BillingCycle, Domain, Invoice, Plan, Subscription, TenantDetail, TenantFlagState, UsagePoint } from '@shared/models';
import { LabelPipe, MoneyPipe } from '@shared/pipes';
import { PagedList, PlatformApi, ToastService, toApiError } from '@shared/services';

// ---------------------------------------------------------------- domains

@Component({
  selector: 'app-tenant-domains',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule, MatIconModule, MatTooltipModule, StatusChipComponent, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form [formGroup]="form" (ngSubmit)="add()" class="mb-4 flex flex-col gap-2 sm:flex-row sm:items-start">
      <mat-form-field class="flex-1" subscriptSizing="dynamic"><mat-label>Custom domain</mat-label>
        <input matInput formControlName="domain" placeholder="play.customer.com" />
        <mat-error><app-field-error [control]="form.controls.domain" /></mat-error></mat-form-field>
      <button mat-flat-button type="submit" class="sm:!mt-2">Add domain</button>
    </form>
    <ul class="m-0 flex list-none flex-col gap-3 p-0">
      @for (d of domains(); track d.id) {
        <li class="rounded-xl border border-outline-variant p-3">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-medium">{{ d.domain }}</span>
            <app-status-chip [status]="d.verification_status" />
            @if (d.is_primary) { <app-status-chip status="active" text="primary" /> }
            <span class="flex-1"></span>
            @if (d.kind === 'custom' && d.verification_status !== 'verified') {
              <button mat-stroked-button type="button" (click)="verify(d)">Check DNS</button>
            }
            @if (d.verification_status === 'verified' && !d.is_primary) {
              <button mat-button type="button" (click)="primary(d)">Make primary</button>
            }
            @if (d.kind === 'custom') {
              <button mat-icon-button type="button" (click)="remove(d)" matTooltip="Remove domain" aria-label="Remove domain"><mat-icon>delete</mat-icon></button>
            }
          </div>
          @if (d.instructions && d.verification_status !== 'verified') {
            <div class="mt-2 grid gap-1 rounded-lg bg-surface-container p-2 font-mono text-xs">
              <span>TXT {{ d.instructions.txt_name }} = "{{ d.instructions.txt_value }}"</span>
              <span>CNAME {{ d.instructions.cname_name }} → {{ d.instructions.cname_value }}</span>
            </div>
          }
        </li>
      }
    </ul>
  `,
})
export class TenantDomainsComponent {
  readonly tenantId = input.required<string>();
  readonly domains = input.required<Domain[]>();
  readonly changed = input.required<() => void>();
  private readonly api = inject(PlatformApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  readonly form = inject(FormBuilder).nonNullable.group({ domain: ['', [Validators.required, Validators.minLength(4)]] });

  add(): void {
    if (this.form.invalid) return;
    this.api.addDomain(this.tenantId(), this.form.getRawValue().domain).subscribe({
      next: () => {
        this.form.reset();
        this.toast.success('Domain added. Publish the DNS records, then check DNS.');
        this.changed()();
      },
      error: (e) => this.toast.error(e),
    });
  }

  verify(d: Domain): void {
    this.api.verifyDomain(d.id).subscribe({
      next: (r) => {
        this.toast.info(r.verification_status === 'verified' ? 'Domain verified' : 'TXT record not found yet');
        this.changed()();
      },
      error: (e) => this.toast.error(e),
    });
  }

  primary(d: Domain): void {
    this.api.primaryDomain(d.id).subscribe({ next: () => this.changed()(), error: (e) => this.toast.error(e) });
  }

  async remove(d: Domain): Promise<void> {
    if ((await this.confirm.ask({ title: 'Remove domain', message: `Stop serving this tenant at ${d.domain}?`, confirmText: 'Remove', destructive: true })) === null) return;
    this.api.removeDomain(d.id).subscribe({ next: () => this.changed()(), error: (e) => this.toast.error(e) });
  }
}

// ---------------------------------------------------------------- subscription

@Component({
  selector: 'app-tenant-subscription',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatSelectModule, MatButtonModule, MatButtonToggleModule, StatusChipComponent, DatePipe, MoneyPipe, EmptyStateComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (sub(); as s) {
      <div class="grid gap-4 md:grid-cols-2">
        <section class="rounded-xl border border-outline-variant p-4">
          <div class="flex items-center gap-2"><h3 class="m-0 text-lg font-semibold">{{ s.plan.name }}</h3><app-status-chip [status]="s.status" /></div>
          <p class="m-0 mt-1 text-sm text-on-surface-variant">{{ s.plan.price_monthly_cents | money: s.plan.currency }}/month · billed {{ s.billing_cycle }}</p>
          <dl class="m-0 mt-3 grid grid-cols-2 gap-2 text-sm">
            <dt class="text-on-surface-variant">Current period</dt><dd class="m-0">{{ s.current_period_start | date: 'mediumDate' }} – {{ s.current_period_end | date: 'mediumDate' }}</dd>
            @if (s.trial_ends_at && s.status === 'trialing') { <dt class="text-on-surface-variant">Trial ends</dt><dd class="m-0">{{ s.trial_ends_at | date: 'medium' }}</dd> }
            @if (s.pending_plan) { <dt class="text-on-surface-variant">Scheduled change</dt><dd class="m-0">{{ s.pending_plan.name }} at period end</dd> }
            @if (s.cancel_at_period_end) { <dt class="text-on-surface-variant">Cancellation</dt><dd class="m-0 text-error">Cancels at period end</dd> }
          </dl>
          <div class="mt-4 flex flex-wrap gap-2">
            @if (s.cancel_at_period_end) {
              <button mat-stroked-button type="button" (click)="resume(s)">Resume</button>
            } @else if (s.status !== 'cancelled' && s.status !== 'expired') {
              <button mat-stroked-button type="button" (click)="cancel(s, false)">Cancel at period end</button>
              <button mat-button type="button" class="!text-error" (click)="cancel(s, true)">Cancel now</button>
            }
          </div>
        </section>
        @if (s.status !== 'cancelled' && s.status !== 'expired') {
          <form [formGroup]="change" (ngSubmit)="changePlan(s)" class="rounded-xl border border-outline-variant p-4">
            <h3 class="m-0 mb-1 text-base font-semibold">Change plan</h3>
            <p class="m-0 mb-3 text-sm text-on-surface-variant">Upgrades apply now with a prorated invoice; downgrades apply at the end of the period.</p>
            <mat-form-field class="w-full"><mat-label>Plan</mat-label>
              <mat-select formControlName="plan_id">@for (p of plans(); track p.id) { <mat-option [value]="p.id">{{ p.name }} — {{ p.price_monthly_cents | money: p.currency }}/mo</mat-option> }</mat-select></mat-form-field>
            <mat-button-toggle-group formControlName="billing_cycle" class="mb-3" aria-label="Billing cycle">
              <mat-button-toggle value="monthly">Monthly</mat-button-toggle><mat-button-toggle value="annual">Annual</mat-button-toggle>
            </mat-button-toggle-group>
            <div><button mat-flat-button type="submit">Apply change</button></div>
          </form>
        }
      </div>
    } @else {
      <app-empty-state icon="autorenew" title="No active subscription" message="Dedicated tenants are licensed instead. Shared tenants need a plan to stay active.">
        <form [formGroup]="change" (ngSubmit)="start()" class="flex flex-wrap items-center justify-center gap-2">
          <mat-form-field subscriptSizing="dynamic"><mat-label>Plan</mat-label>
            <mat-select formControlName="plan_id">@for (p of plans(); track p.id) { <mat-option [value]="p.id">{{ p.name }}</mat-option> }</mat-select></mat-form-field>
          <button mat-flat-button type="submit">Start subscription</button>
        </form>
      </app-empty-state>
    }
  `,
})
export class TenantSubscriptionComponent {
  readonly tenantId = input.required<string>();
  readonly changed = input.required<() => void>();
  private readonly api = inject(PlatformApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  readonly sub = signal<Subscription | null>(null);
  readonly plans = signal<Plan[]>([]);
  readonly change = inject(FormBuilder).nonNullable.group({ plan_id: ['', Validators.required], billing_cycle: ['monthly' as BillingCycle] });

  constructor() {
    effect(() => this.load(this.tenantId()));
    this.api.plans().subscribe((p) => this.plans.set(p.filter((x) => x.kind === 'saas' && x.is_active)));
  }

  load(tenantId: string): void {
    this.api.subscriptions({ tenant_id: tenantId, page_size: 1 }).subscribe((page) => {
      const live = page.items[0] ?? null;
      this.sub.set(live);
      if (live) this.change.patchValue({ plan_id: live.plan.id, billing_cycle: live.billing_cycle });
    });
  }

  start(): void {
    const v = this.change.getRawValue();
    if (!v.plan_id) return;
    this.api.startSubscription(this.tenantId(), { plan_id: v.plan_id, billing_cycle: v.billing_cycle, with_trial: false }).subscribe({
      next: () => this.done('Subscription started and first invoice issued'),
      error: (e) => this.toast.error(e),
    });
  }

  changePlan(s: Subscription): void {
    const v = this.change.getRawValue();
    this.api.changePlan(s.id, v).subscribe({
      next: (r) => this.done(r.mode === 'downgrade_scheduled' ? 'Downgrade scheduled for period end' : 'Plan changed'),
      error: (e) => this.toast.error(e),
    });
  }

  async cancel(s: Subscription, immediately: boolean): Promise<void> {
    const ok = await this.confirm.ask({
      title: immediately ? 'Cancel immediately' : 'Cancel at period end',
      message: immediately ? 'The workspace is suspended right away.' : `The workspace stays active until ${new Date(s.current_period_end).toLocaleDateString()}.`,
      confirmText: 'Cancel subscription',
      destructive: true,
    });
    if (ok === null) return;
    this.api.cancelSubscription(s.id, immediately).subscribe({ next: () => this.done('Subscription cancelled'), error: (e) => this.toast.error(e) });
  }

  resume(s: Subscription): void {
    this.api.resumeSubscription(s.id).subscribe({ next: () => this.done('Cancellation withdrawn'), error: (e) => this.toast.error(e) });
  }

  private done(message: string): void {
    this.toast.success(message);
    this.load(this.tenantId());
    this.changed()();
  }
}

// ---------------------------------------------------------------- page

const USAGE_METRICS = ['api_requests', 'active_accounts', 'accounts_total', 'point_transactions', 'matches_completed', 'events_created'];

@Component({
  selector: 'app-owner-tenant-detail',
  imports: [
    RouterLink, ReactiveFormsModule, DatePipe, LabelPipe, MoneyPipe,
    MatTabsModule, MatButtonModule, MatIconModule, MatMenuModule, MatFormFieldModule, MatInputModule, MatSelectModule, MatButtonToggleModule,
    PageHeaderComponent, StatusChipComponent, LoadingComponent, ErrorStateComponent, DataTableComponent, CellDefDirective, BarChartComponent,
    FieldErrorComponent, TenantDomainsComponent, TenantSubscriptionComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (error()) {
      <app-error-state [message]="error()!" (retry)="load()" />
    } @else if (!tenant()) {
      <app-loading />
    } @else {
      @let t = tenant()!;
      <app-page-header [title]="t.name" [subtitle]="t.primary_domain">
        <a breadcrumb routerLink="/tenants" class="mb-1 inline-flex items-center gap-1 text-sm text-primary no-underline"><mat-icon class="!h-4 !w-4 !text-base">arrow_back</mat-icon>Tenants</a>
        <app-status-chip [status]="t.status" />
        @if (t.status === 'active') {
          <button mat-stroked-button type="button" (click)="suspend()"><mat-icon>block</mat-icon>Suspend</button>
        } @else if (t.status === 'suspended') {
          <button mat-flat-button type="button" (click)="activate()"><mat-icon>play_arrow</mat-icon>Activate</button>
        }
        <button mat-icon-button type="button" [matMenuTriggerFor]="more" aria-label="More actions"><mat-icon>more_vert</mat-icon></button>
        <mat-menu #more="matMenu"><button mat-menu-item type="button" class="!text-error" (click)="archive()"><mat-icon>archive</mat-icon>Cancel and archive</button></mat-menu>
      </app-page-header>
      @if (t.suspended_reason) {
        <p class="m-0 mb-4 rounded-lg bg-error-container p-3 text-sm text-on-error-container" role="status">Suspended: {{ t.suspended_reason | label }} · {{ t.suspended_at | date: 'medium' }}</p>
      }

      <mat-tab-group mat-stretch-tabs="false" animationDuration="0ms">
        <mat-tab label="Overview">
          <div class="grid gap-4 py-4 md:grid-cols-3">
            <div class="rounded-xl border border-outline-variant p-4"><p class="m-0 text-xs text-on-surface-variant">Plan</p>
              <p class="m-0 mt-1 text-lg font-semibold">{{ t.plan_name ?? (t.deployment_mode === 'dedicated' ? 'Licensed install' : 'None') }}</p>
              @if (t.subscription_status) { <app-status-chip [status]="t.subscription_status" /> }</div>
            <div class="rounded-xl border border-outline-variant p-4"><p class="m-0 text-xs text-on-surface-variant">Accounts</p>
              <p class="m-0 mt-1 text-lg font-semibold tabular-nums">{{ t.user_count }}</p></div>
            <div class="rounded-xl border border-outline-variant p-4"><p class="m-0 text-xs text-on-surface-variant">Created</p>
              <p class="m-0 mt-1 text-lg font-semibold">{{ t.created_at | date: 'mediumDate' }}</p>
              <p class="m-0 text-xs text-on-surface-variant">{{ t.deployment_mode | label }} · slug {{ t.slug }}</p></div>
          </div>
          @if (t.settings; as s) {
            <dl class="m-0 grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
              <div><dt class="text-on-surface-variant">Timezone</dt><dd class="m-0">{{ s.timezone }}</dd></div>
              <div><dt class="text-on-surface-variant">Self-registration</dt><dd class="m-0">{{ s.allow_self_registration ? 'Open' : 'Closed' }}</dd></div>
              <div><dt class="text-on-surface-variant">Grace period</dt><dd class="m-0">{{ s.suspension_grace_days }} days</dd></div>
              <div><dt class="text-on-surface-variant">Modules</dt><dd class="m-0">{{ s.enabled_modules.join(', ') }}</dd></div>
            </dl>
          }
        </mat-tab>

        <mat-tab label="Domains">
          <div class="py-4"><app-tenant-domains [tenantId]="t.id" [domains]="t.domains" [changed]="reload" /></div>
        </mat-tab>

        <mat-tab label="Branding">
          <form [formGroup]="branding" (ngSubmit)="saveBranding()" class="grid max-w-3xl grid-cols-1 gap-x-3 py-4 sm:grid-cols-2" novalidate>
            <mat-form-field class="sm:col-span-2"><mat-label>Application name</mat-label><input matInput formControlName="app_name" />
              <mat-error><app-field-error [control]="branding.controls.app_name" /></mat-error></mat-form-field>
            @for (c of colorFields; track c.key) {
              <mat-form-field><mat-label>{{ c.label }}</mat-label>
                <input matInput [formControlName]="c.key" />
                <input matSuffix type="color" class="mr-2 h-7 w-9 cursor-pointer border-0 bg-transparent" [formControlName]="c.key" [attr.aria-label]="c.label + ' picker'" />
                <mat-error><app-field-error [control]="branding.get(c.key)" /></mat-error></mat-form-field>
            }
            <mat-form-field><mat-label>Login title</mat-label><input matInput formControlName="login_title" /></mat-form-field>
            <mat-form-field><mat-label>Login subtitle</mat-label><input matInput formControlName="login_subtitle" /></mat-form-field>
            <mat-form-field><mat-label>Contact email</mat-label><input matInput formControlName="contact_email" /></mat-form-field>
            <mat-form-field><mat-label>Contact phone</mat-label><input matInput formControlName="contact_phone" /></mat-form-field>
            <div class="sm:col-span-2"><button mat-flat-button type="submit">Save branding</button></div>
          </form>
        </mat-tab>

        <mat-tab label="Subscription">
          <div class="py-4"><app-tenant-subscription [tenantId]="t.id" [changed]="reload" /></div>
        </mat-tab>

        <mat-tab label="Invoices">
          <div class="py-4">
            <app-data-table [columns]="invoiceColumns" [rows]="invoices.rows()" [total]="invoices.total()" [page]="invoices.page()" [pageSize]="invoices.pageSize()"
                            [loading]="invoices.loading()" [error]="invoices.error()" [searchable]="false" emptyTitle="No invoices" emptyIcon="receipt_long"
                            (pageChange)="invoices.setPage($event)" (retry)="invoices.load()">
              <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
              <ng-template appCell="total_cents" let-row>{{ row.total_cents | money: row.currency }}</ng-template>
              <ng-template appCell="issued_at" let-row>{{ row.issued_at | date: 'mediumDate' }}</ng-template>
            </app-data-table>
          </div>
        </mat-tab>

        <mat-tab label="Feature flags">
          <ul class="m-0 flex list-none flex-col gap-2 p-0 py-4">
            @for (f of flags(); track f.key) {
              <li class="flex flex-col gap-2 rounded-xl border border-outline-variant p-3 sm:flex-row sm:items-center">
                <div class="min-w-0 flex-1"><p class="m-0 font-mono text-sm font-medium">{{ f.key }}</p>
                  <p class="m-0 text-xs text-on-surface-variant">{{ f.description }} · default {{ f.default_enabled ? 'on' : 'off' }}</p></div>
                <mat-button-toggle-group [value]="f.override === null ? 'default' : f.override ? 'on' : 'off'" (change)="setFlag(f, $event.value)" [attr.aria-label]="f.key">
                  <mat-button-toggle value="default">Default</mat-button-toggle><mat-button-toggle value="on">On</mat-button-toggle><mat-button-toggle value="off">Off</mat-button-toggle>
                </mat-button-toggle-group>
              </li>
            } @empty { <li class="text-sm text-on-surface-variant">No feature flags defined.</li> }
          </ul>
        </mat-tab>

        <mat-tab label="Usage">
          <div class="flex flex-col gap-4 py-4">
            <mat-form-field class="w-64" subscriptSizing="dynamic"><mat-label>Metric</mat-label>
              <mat-select [value]="metric()" (selectionChange)="metric.set($event.value)">
                @for (m of metrics; track m) { <mat-option [value]="m">{{ m | label }}</mat-option> }
              </mat-select></mat-form-field>
            <app-bar-chart [title]="(metric() | label) + ' per day, last 30 days'" [data]="usageSeries()" />
          </div>
        </mat-tab>

        <mat-tab label="Audit">
          <div class="py-4">
            <app-data-table [columns]="auditColumns" [rows]="audit.rows()" [total]="audit.total()" [page]="audit.page()" [pageSize]="audit.pageSize()"
                            [loading]="audit.loading()" [error]="audit.error()" searchPlaceholder="Search actions" emptyTitle="No audit entries"
                            (search)="audit.setSearch($event)" (pageChange)="audit.setPage($event)" (retry)="audit.load()">
              <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'medium' }}</ng-template>
            </app-data-table>
          </div>
        </mat-tab>
      </mat-tab-group>
    }
  `,
})
export class TenantDetailPage {
  readonly id = input.required<string>();
  private readonly api = inject(PlatformApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly router = inject(Router);

  readonly tenant = signal<TenantDetail | null>(null);
  readonly error = signal<string | null>(null);
  readonly flags = signal<TenantFlagState[]>([]);
  readonly usage = signal<UsagePoint[]>([]);
  readonly metric = signal('api_requests');
  readonly metrics = USAGE_METRICS;
  readonly reload = () => this.load();

  readonly invoices = new PagedList<Invoice>((q) => this.api.invoices({ ...q, tenant_id: this.id() }));
  readonly audit = new PagedList<AuditEntry>((q) => this.api.audit({ ...q, tenant_id: this.id() }));
  readonly invoiceColumns: Column<Invoice>[] = [
    { key: 'number', label: 'Number' },
    { key: 'status', label: 'Status' },
    { key: 'total_cents', label: 'Total', align: 'end' },
    { key: 'issued_at', label: 'Issued', hideOnMobile: true },
  ];
  readonly auditColumns: Column<AuditEntry>[] = [
    { key: 'created_at', label: 'When' },
    { key: 'action', label: 'Action' },
    { key: 'actor_label', label: 'Actor', hideOnMobile: true },
    { key: 'resource_type', label: 'Resource', hideOnMobile: true },
  ];
  readonly colorFields = [
    { key: 'primary_color', label: 'Primary colour' },
    { key: 'secondary_color', label: 'Secondary colour' },
    { key: 'background_color', label: 'Background colour' },
    { key: 'surface_color', label: 'Surface colour' },
  ];
  private readonly hex = Validators.pattern(/^#[0-9a-fA-F]{6}$/);
  readonly branding = inject(FormBuilder).nonNullable.group({
    app_name: ['', [Validators.required, Validators.maxLength(120)]],
    primary_color: [DEFAULT_BRAND_COLORS.primary, [Validators.required, this.hex]],
    secondary_color: [DEFAULT_BRAND_COLORS.secondary, [Validators.required, this.hex]],
    background_color: [DEFAULT_BRAND_COLORS.background, [Validators.required, this.hex]],
    surface_color: [DEFAULT_BRAND_COLORS.surface, [Validators.required, this.hex]],
    login_title: [''],
    login_subtitle: [''],
    contact_email: [''],
    contact_phone: [''],
  });

  readonly usageSeries = computed(() => {
    const byDay = new Map(this.usage().filter((u) => u.metric === this.metric()).map((u) => [u.period_date, u.value]));
    const out = [];
    for (let i = 29; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86_400_000).toISOString().slice(0, 10);
      out.push({ label: d.slice(5), value: byDay.get(d) ?? 0 });
    }
    return out;
  });

  constructor() {
    effect(() => {
      this.id();
      this.load();
      this.invoices.load();
      this.audit.load();
    });
  }

  load(): void {
    this.error.set(null);
    this.api.tenant(this.id()).subscribe({
      next: (t) => {
        this.tenant.set(t);
        if (t.branding) this.branding.patchValue(Object.fromEntries(Object.entries(t.branding).map(([k, v]) => [k, v ?? ''])));
      },
      error: (e) => this.error.set(toApiError(e).message),
    });
    this.api.tenantFlags(this.id()).subscribe((f) => this.flags.set(f));
    this.api.usage(this.id(), 30).subscribe((u) => this.usage.set(u));
  }

  async suspend(): Promise<void> {
    const reason = await this.confirm.ask({ title: 'Suspend tenant', message: 'All users of this workspace lose access until it is reactivated.',
      confirmText: 'Suspend', destructive: true, inputLabel: 'Reason', inputRequired: true });
    if (reason === null) return;
    this.api.suspendTenant(this.id(), reason).subscribe({ next: () => (this.toast.success('Tenant suspended'), this.load()), error: (e) => this.toast.error(e) });
  }

  activate(): void {
    this.api.activateTenant(this.id()).subscribe({ next: () => (this.toast.success('Tenant activated'), this.load()), error: (e) => this.toast.error(e) });
  }

  async archive(): Promise<void> {
    const ok = await this.confirm.ask({ title: 'Cancel and archive tenant', message: 'The workspace is cancelled permanently. Data is retained for audit.',
      confirmText: 'Archive tenant', destructive: true });
    if (ok === null) return;
    this.api.deleteTenant(this.id()).subscribe({ next: () => void this.router.navigate(['/tenants']), error: (e) => this.toast.error(e) });
  }

  saveBranding(): void {
    if (this.branding.invalid) {
      this.branding.markAllAsTouched();
      return;
    }
    const raw = this.branding.getRawValue();
    const body = Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, v === '' ? null : v]));
    this.api.updateBranding(this.id(), body).subscribe({ next: () => this.toast.success('Branding saved'), error: (e) => this.toast.error(e) });
  }

  setFlag(flag: TenantFlagState, value: 'default' | 'on' | 'off'): void {
    this.api.setTenantFlags(this.id(), { [flag.key]: value === 'default' ? null : value === 'on' }).subscribe({
      next: (f) => this.flags.set(f),
      error: (e) => this.toast.error(e),
    });
  }
}

import { TestBed } from '@angular/core/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { of } from 'rxjs';
import type { Plan } from '@shared/models';
import { MatDialog } from '@angular/material/dialog';
import { PlatformApi, ToastService } from '@shared/services';
import { PlanDialog, PlansPage } from './plans';

function dialog(plan: Partial<Plan> | null, api: Partial<PlatformApi> = {}) {
  const close = vi.fn();
  TestBed.configureTestingModule({ providers: [
    { provide: MAT_DIALOG_DATA, useValue: plan },
    { provide: PlatformApi, useValue: api },
    { provide: MatDialogRef, useValue: { close } },
  ] });
  return { d: TestBed.runInInjectionContext(() => new PlanDialog()), close };
}

describe('PlanDialog billing currency', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('creates PHP plans only and never pre-fills a price', () => {
    const createPlan = vi.fn((body) => of({ id: 'p1', ...body }));
    const { d, close } = dialog(null, { createPlan } as Partial<PlatformApi>);
    expect(d.currency).toBe('PHP');
    expect(d.currencyLabel).toBe('PHP · Philippine Peso');
    expect(d.symbol).toBe('₱');
    expect(d.form.controls.monthly.value).toBeNull();
    expect(d.form.controls.annual.value).toBeNull();

    d.form.patchValue({ code: 'venue', name: 'Venue' });
    d.save();
    expect(createPlan).not.toHaveBeenCalled(); // prices are required
    expect(d.form.controls.monthly.hasError('required')).toBe(true);

    d.form.patchValue({ monthly: 1250, annual: 12500.5 });
    d.save();
    expect(createPlan).toHaveBeenCalledOnce();
    const body = createPlan.mock.calls[0][0];
    expect(body.currency).toBe('PHP');
    expect(body.price_monthly_cents).toBe(125000);
    expect(body.price_annual_cents).toBe(1250050);
    expect(body.approve_prices).toBe(false); // never approved implicitly
    expect(close).toHaveBeenCalled();
  });

  it('opens a PHP replacement without treating the legacy USD amounts as peso prices', () => {
    const { d } = dialog({ id: 'p0', code: 'starter', name: 'Starter', kind: 'saas', currency: 'USD', price_monthly_cents: 4900,
      price_annual_cents: 49000, trial_days: 14, sort_order: 10, limits: {}, modules: [], is_active: true } as unknown as Plan);
    expect(d.currency).toBe('PHP');
    expect(d.legacyCurrency).toBe(true);
    expect(d.symbol).toBe('₱');
    expect(d.form.controls.monthly.value).toBeNull();
    expect(d.form.controls.annual.value).toBeNull();
    expect(d.form.controls.code.value).toBe('starter-php');
  });
});

describe('PlanDialog price approval', () => {
  afterEach(() => TestBed.resetTestingModule());
  const approved = { id: 'p1', code: 'venue', name: 'Venue', kind: 'saas', currency: 'PHP', price_monthly_cents: 125000,
    price_annual_cents: 1250000, trial_days: 0, sort_order: 0, limits: {}, modules: [], is_active: true,
    prices_approved_at: '2026-11-01T00:00:00Z', review_reasons: [], checkout_ready: true } as unknown as Plan;

  it('sends approval only when the owner ticks it, and warns when a price change withdraws it', () => {
    const updatePlan = vi.fn((_id, body) => of({ ...approved, ...body }));
    const { d } = dialog(approved, { updatePlan } as Partial<PlatformApi>);
    expect(d.approvalHint()).toContain('Current prices are approved');
    d.form.patchValue({ monthly: 1500 });
    expect(d.approvalHint()).toContain('stops being sold');
    d.save();
    expect(updatePlan.mock.calls[0][1].approve_prices).toBe(false);
  });

  it('creates an approved PHP replacement without modifying the legacy plan', () => {
    const updatePlan = vi.fn((_id, body) => of(body));
    const createPlan = vi.fn(body => of(body));
    const { d } = dialog({ ...approved, currency: 'USD', prices_approved_at: null } as Plan, { updatePlan, createPlan } as Partial<PlatformApi>);
    d.form.patchValue({ monthly:15000, annual:150000, approve_prices: true });
    d.save();
    expect(updatePlan).not.toHaveBeenCalled();
    expect(createPlan).toHaveBeenCalledWith(expect.objectContaining({currency:'PHP',code:'venue-php',price_monthly_cents:1500000,approve_prices:true}));
  });
});

describe('PlansPage sale status', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('labels plans for sale, awaiting approval, legacy and retired, and summarises what needs review', () => {
    const base = { kind: 'saas', price_monthly_cents: 0, price_annual_cents: 0, trial_days: 0, sort_order: 0, limits: {}, modules: [] };
    const rows = [
      { ...base, id: 'a', code: 'a', name: 'Ready', currency: 'PHP', is_active: true, checkout_ready: true, review_reasons: [] },
      { ...base, id: 'b', code: 'b', name: 'Draft', currency: 'PHP', is_active: true, checkout_ready: false, review_reasons: ['prices_unapproved'] },
      { ...base, id: 'c', code: 'c', name: 'Old', currency: 'USD', is_active: true, checkout_ready: false, review_reasons: ['legacy_currency', 'prices_unapproved'] },
      { ...base, id: 'd', code: 'd', name: 'Gone', currency: 'PHP', is_active: false, checkout_ready: false, review_reasons: ['prices_unapproved', 'inactive'] },
    ] as unknown as Plan[];
    TestBed.configureTestingModule({ providers: [
      { provide: PlatformApi, useValue: { plans: () => of(rows) } },
      { provide: MatDialog, useValue: {} },
      { provide: ToastService, useValue: {} },
    ] });
    const page = TestBed.runInInjectionContext(() => new PlansPage());
    expect(rows.map((r) => page.saleStatus(r).text)).toEqual(['for sale', 'needs price approval', 'legacy USD · review', 'retired']);
    expect(page.needsReview().map((p) => p.name)).toEqual(['Draft', 'Old']);
    expect(page.reviewSummary()).toBe('Draft (prices not approved); Old (legacy currency, prices not approved)');
  });
});

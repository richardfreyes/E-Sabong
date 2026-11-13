import { TestBed } from '@angular/core/testing';
import { of, Subject, throwError } from 'rxjs';
import { ApiClient } from '@shared/services';
import { RentalPage } from './rental';

describe('OWNER rental controls', () => {
  afterEach(() => TestBed.resetTestingModule());
  async function setup() {
    const api = {
      get: vi.fn((path:string) => of(path.endsWith('/settings') ? {shared_domain_enabled:false,default_tenant_id:null,broadcast_tenant_id:null,global_event_id:null,global_stream_id:null}
        : path.endsWith('/tenants') || path.endsWith('/accounts') ? {items:[],total:0} : path.endsWith('/summary') ? {monthly_cents:1500025,accounts:{player:2}} : [])),
      put: vi.fn(() => of({})), post: vi.fn(() => of({})), patch: vi.fn(() => of({})),
    };
    TestBed.configureTestingModule({providers:[{provide:ApiClient,useValue:api}]});
    const page = TestBed.runInInjectionContext(() => new RentalPage());
    await vi.waitFor(() => expect(page.busy()).toBe(false));
    return {page,api};
  }
  it('saves the selected tenant fee in exact cents without submitting a second request', async () => {
    const {page,api} = await setup();
    const pending = new Subject<object>();
    api.put.mockReturnValue(pending);
    page.tenantId='tenant-two'; page.monthly=15000.25;
    const saving = page.savePrice();
    await page.savePrice();
    expect(api.put).toHaveBeenCalledOnce();
    expect(api.put).toHaveBeenCalledWith('/platform/rental/tenants/tenant-two/price',{
      monthly_cents:1500025,next_billing_at:null,activation_at:null,
    });
    pending.next({}); pending.complete(); await saving;
    expect(page.notice()).toBe('Changes saved');
    expect(page.summary()?.accounts['player']).toBe(2);
  });
  it('rejects invalid rental amounts and surfaces backend errors', async () => {
    const {page,api} = await setup();
    page.tenantId='tenant-two'; page.monthly=-1;
    await page.savePrice();
    expect(api.put).not.toHaveBeenCalled();
    api.put.mockReturnValue(throwError(() => new Error('Save failed')));
    page.monthly=100;
    await page.savePrice();
    expect(page.error()).toBeTruthy();
    expect(page.notice()).toBe('');
    expect(page.busy()).toBe(false);
  });
  it('suspends only the selected tenant and sends stream control through OWNER endpoints', async () => {
    const {page,api} = await setup();
    page.tenantId='tenant-two';
    await page.status('suspend');
    expect(api.post).toHaveBeenCalledWith('/platform/tenants/tenant-two/suspend',{reason:'Suspended by OWNER'});
    page.config.global_stream_id='global-stream';
    await page.streamEnabled(false);
    expect(api.patch).toHaveBeenCalledWith('/platform/rental/streams/global-stream',{enabled:false});
  });
  it('does not show an old tenant fee after quickly selecting another tenant', async () => {
    const {page,api} = await setup();
    const first=new Subject<{monthly_cents:number;accounts:{player:number}}>();
    const second=new Subject<{monthly_cents:number;accounts:{player:number}}>();
    api.get.mockImplementation((path:string) => path.includes('tenant-one/summary') ? first : path.includes('tenant-two/summary') ? second : of({items:[],total:0}));
    page.tenantId='tenant-one'; const old=page.loadSummary();
    page.tenantId='tenant-two'; const latest=page.loadSummary();
    second.next({monthly_cents:1500000,accounts:{player:2}}); second.complete(); await latest;
    first.next({monthly_cents:1000000,accounts:{player:1}}); first.complete(); await old;
    expect(page.monthly).toBe(15000);
    expect(page.summary()?.accounts['player']).toBe(2);
    expect(page.loadingTenant()).toBe(false);
  });
});

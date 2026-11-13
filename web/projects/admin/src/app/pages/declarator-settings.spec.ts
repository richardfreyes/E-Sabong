import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { QuickBetSettingsApi, QuickBetSettings, defaultQuickBets, ToastService, quickBetLabel } from '@shared/services';
import { DeclaratorSettingsComponent } from './declarator-settings';

function setup() {
  const api = { get: vi.fn(() => of(defaultQuickBets())), save: vi.fn((value: QuickBetSettings) => of(value)) };
  const toast = { success: vi.fn(), error: vi.fn() };
  TestBed.configureTestingModule({providers:[{provide:QuickBetSettingsApi,useValue:api},{provide:ToastService,useValue:toast}]});
  const fixture = TestBed.createComponent(DeclaratorSettingsComponent);
  fixture.detectChanges();
  return {fixture, component:fixture.componentInstance, api, toast};
}
describe('Declarator Settings',()=>{
  it('loads persisted amounts, saves independent sides, and resets through the backend',()=>{
    const {component,api,toast}=setup();
    component.form.controls.meron.at(0).setValue('7000.00');
    component.save();
    expect(api.save).toHaveBeenCalledWith({...defaultQuickBets(),meron:['7000.00','10000.00','20000.00','50000.00']});
    expect(toast.success).toHaveBeenCalled();
    component.reset();
    expect(api.save).toHaveBeenLastCalledWith(defaultQuickBets());
    expect(component.form.getRawValue()).toEqual(defaultQuickBets());
  });
  it('rejects invalid amounts and reports save errors',()=>{
    const {component,api,toast}=setup();
    for (const value of ['0','-1','NaN','1.001','']) {
      component.form.controls.wala.at(0).setValue(value); component.save();
    }
    expect(api.save).not.toHaveBeenCalled();
    component.form.setValue(defaultQuickBets());
    api.save.mockReturnValue(throwError(()=>new Error('failed')));
    component.save();
    expect(toast.error).toHaveBeenCalled();
    expect(component.saving()).toBe(false);
  });
  it('prevents duplicate saves while a request is pending',()=>{
    const {component,api}=setup();
    const pending = new Subject<QuickBetSettings>(); api.save.mockReturnValue(pending);
    component.save(); component.save();
    expect(api.save).toHaveBeenCalledTimes(1);
    pending.next(defaultQuickBets()); pending.complete();
    expect(component.saving()).toBe(false);
  });
  it('formats whole thousands and preserves other precise amounts',()=>{
    expect(quickBetLabel('7000.00')).toBe('7K');
    expect(quickBetLabel('100000.00')).toBe('100K');
    expect(quickBetLabel('1250.25')).toBe('1,250.25');
  });
});

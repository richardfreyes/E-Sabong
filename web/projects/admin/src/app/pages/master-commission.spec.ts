import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { of, throwError } from 'rxjs';
import { CommissionApi, ToastService } from '@shared/services';
import { MasterCommissionCard, toFraction, toPercent } from './master-commission';

describe('MasterCommissionCard', () => {
  const base = { master_agent_id: 'm1', username: 'master1', allocation: '0.0850', is_default: true, highest_agent_rate: '0.0300' };

  function render(api: Partial<Record<'masterAllocation' | 'setMasterAllocation', ReturnType<typeof vi.fn>>>) {
    const toast = { success: vi.fn(), error: vi.fn() };
    const commission = { masterAllocation: vi.fn(() => of(base)), setMasterAllocation: vi.fn(), ...api };
    TestBed.configureTestingModule({
      providers: [provideNoopAnimations(), { provide: CommissionApi, useValue: commission }, { provide: ToastService, useValue: toast }],
    });
    const fixture = TestBed.createComponent(MasterCommissionCard);
    fixture.componentRef.setInput('masterId', 'm1');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    return { fixture, el, commission, toast };
  }

  function type(el: HTMLElement, value: string) {
    const input = el.querySelector('[data-field="commission-input"]') as HTMLInputElement;
    input.value = value;
    input.dispatchEvent(new Event('input'));
  }

  it('converts between percent and the API fraction', () => {
    expect(toPercent('0.0850')).toBe('8.5');
    expect(toPercent('1.0000')).toBe('100');
    expect(toFraction('8.5')).toBe('0.0850');
    expect(toFraction('12.34')).toBe('0.1234');
  });

  it('shows the master agent, the 8.5% default and the editable field', () => {
    const { el } = render({});
    const card = el.querySelector('[data-testid="master-commission"]')!.textContent!;
    expect(card).toContain('master1');
    expect(card).toContain('Master Agent');
    expect(el.querySelector('[data-field="current-rate"]')!.textContent).toContain('8.5%');
    expect(el.querySelector('[data-field="current-rate"]')!.textContent).toContain('default');
    expect((el.querySelector('[data-field="commission-input"]') as HTMLInputElement).value).toBe('8.5');
    // Nothing changed yet: Save is disabled.
    expect((el.querySelector('[data-action="save-commission"]') as HTMLButtonElement).disabled).toBe(true);
  });

  it('saves a new rate, notifies and shows it at once', () => {
    const saved = { ...base, allocation: '0.0700', is_default: false };
    const { fixture, el, commission, toast } = render({ setMasterAllocation: vi.fn(() => of(saved)) });
    type(el, '7');
    fixture.detectChanges();
    (el.querySelector('[data-action="save-commission"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(commission.setMasterAllocation).toHaveBeenCalledWith('m1', '0.0700');
    expect(toast.success).toHaveBeenCalledWith('Commission for master1 set to 7%.');
    expect(el.querySelector('[data-field="current-rate"]')!.textContent).toContain('7%');
    expect(el.querySelector('[data-field="current-rate"]')!.textContent).not.toContain('default');
  });

  it('refuses values outside 0 to 100 before calling the API', () => {
    const { fixture, el, commission } = render({});
    for (const bad of ['101', '-1', '8.555']) {
      type(el, bad);
      fixture.detectChanges();
      expect((el.querySelector('[data-action="save-commission"]') as HTMLButtonElement).disabled).toBe(true);
    }
    expect(commission.setMasterAllocation).not.toHaveBeenCalled();
  });

  it('shows the server error and keeps the current rate', () => {
    const failure = throwError(() => ({ status: 409, error: { error: { code: 'allocation_below_agent_rate', message: 'Lower that agent first' } } }));
    const { fixture, el, toast } = render({ setMasterAllocation: vi.fn(() => failure) });
    type(el, '2');
    fixture.detectChanges();
    (el.querySelector('[data-action="save-commission"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(toast.error).toHaveBeenCalled();
    expect(el.querySelector('[data-field="current-rate"]')!.textContent).toContain('8.5%');
    expect(el.textContent).toContain('Cannot be lower than 3%');
  });
});

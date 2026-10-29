import { TestBed } from '@angular/core/testing';
import { AmountPickerComponent } from './amount-picker';

/** Validation must not turn an invalid input into a stored demonstration amount. */
describe('AmountPickerComponent', () => {
  function setup() {
    const fixture = TestBed.createComponent(AmountPickerComponent);
    fixture.componentRef.setInput('amount', 100);
    fixture.componentRef.setInput('available', 1000);
    fixture.detectChanges();
    const input = fixture.nativeElement.querySelector('input') as HTMLInputElement;
    const emitted = vi.fn();
    fixture.componentInstance.amountChange.subscribe(emitted);
    return { fixture, input, emitted };
  }

  it('rejects empty, fractional, negative and over-balance values without emitting', () => {
    const { fixture, input, emitted } = setup();
    for (const raw of ['', '1.5', '-5', '1001']) {
      input.value = raw;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
      expect(input.getAttribute('aria-invalid')).toBe('true');
      expect(fixture.componentInstance.error()).not.toBe('');
    }
    expect(emitted).not.toHaveBeenCalled();
  });

  it('retains the error on blur and recovers with a quick amount', () => {
    const { fixture, input, emitted } = setup();
    input.value = '2000';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new Event('blur'));
    fixture.detectChanges();
    expect(fixture.componentInstance.error()).toContain('1,000');
    fixture.nativeElement.querySelector('button').click();
    fixture.detectChanges();
    expect(fixture.componentInstance.error()).toBe('');
    expect(input.value).toBe('50');
    expect(emitted).toHaveBeenCalledWith(50);
  });

  it('accepts the available balance and disables amounts above it', () => {
    const { fixture, input, emitted } = setup();
    input.value = '1000';
    input.dispatchEvent(new Event('input'));
    expect(emitted).toHaveBeenCalledWith(1000);
    fixture.componentRef.setInput('available', 75);
    fixture.detectChanges();
    const buttons = Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[];
    expect(buttons.map(b => b.disabled)).toEqual([false, true, true, true]);
  });
});

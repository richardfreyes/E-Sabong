import { TestBed } from '@angular/core/testing';

import { COUNT_UP_MS, CountUpComponent } from './count-up';

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe('CountUpComponent', () => {
  afterEach(() => vi.unstubAllGlobals());

  function setup(value: string | null) {
    const fixture = TestBed.createComponent(CountUpComponent);
    fixture.componentRef.setInput('value', value);
    fixture.detectChanges();
    const text = () => {
      fixture.detectChanges();
      return (fixture.nativeElement as HTMLElement).textContent!.trim();
    };
    return { fixture, text };
  }

  it('shows the first value at once', () => {
    const { text } = setup('7111.06');
    expect(text()).toBe('7,111.06');
  });

  it('counts up through intermediate figures, then rests on the new value', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    const { fixture, text } = setup('0.00');
    fixture.componentRef.setInput('value', '100.00');
    expect(text()).toBe('0.00');
    await wait(COUNT_UP_MS / 4);
    const mid = Number(text().replace(/,/g, ''));
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(100);
    await wait(COUNT_UP_MS);
    expect(text()).toBe('100.00');
  });

  it('retargets from the displayed amount and reaches the exact latest cents', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    const { fixture, text } = setup('730165.00');
    fixture.componentRef.setInput('value', '735165.00'); text();
    await wait(COUNT_UP_MS / 3);
    const before = Number(text().replace(/,/g, ''));
    fixture.componentRef.setInput('value', '745165.25');
    expect(Number(text().replace(/,/g, ''))).toBeGreaterThanOrEqual(before);
    await wait(COUNT_UP_MS + 100);
    expect(text()).toBe('745,165.25');
  });

  it('snaps when the value drops, is unavailable or the user prefers reduced motion', () => {
    const { fixture, text } = setup('500.00');
    fixture.componentRef.setInput('value', '0.00');
    expect(text()).toBe('0.00');
    fixture.componentRef.setInput('value', null);
    expect(text()).toBe('—');

    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    fixture.componentRef.setInput('value', '10.00');
    expect(text()).toBe('10.00');
    fixture.componentRef.setInput('value', '900.00');
    expect(text()).toBe('900.00');
  });
});

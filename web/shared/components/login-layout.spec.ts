import { FormControl, Validators } from '@angular/forms';
import { TestBed } from '@angular/core/testing';
import { BRAND_REVEAL_MS, BrandLogoComponent } from './brand-logo';
import { LoginLayoutComponent } from './login-layout';

describe('BrandLogoComponent', () => {
  beforeEach(() => sessionStorage.clear());

  function logo(variant: 'static' | 'compact' | 'reveal' | 'loop', src: string | null = null) {
    const fixture = TestBed.createComponent(BrandLogoComponent);
    fixture.componentRef.setInput('variant', variant);
    fixture.componentRef.setInput('src', src);
    fixture.detectChanges();
    return fixture.nativeElement.querySelector('img') as HTMLImageElement;
  }

  it('plays the reveal once per session, then shows the static logo', () => {
    expect(logo('reveal').getAttribute('src')).toBe('assets/brand/e-sabong-logo-reveal.svg');
    expect(logo('reveal').getAttribute('src')).toBe('assets/brand/e-sabong-logo.svg');
  });

  it('uses the compact and loop artwork and prefers a tenant logo', () => {
    expect(logo('compact').getAttribute('src')).toBe('assets/brand/e-sabong-logo-compact.svg');
    expect(logo('loop').getAttribute('src')).toBe('assets/brand/e-sabong-logo-loop.svg');
    expect(logo('static', '/api/media/logo.png').getAttribute('src')).toBe('/api/media/logo.png');
  });
});

describe('LoginLayoutComponent', () => {
  beforeEach(() => sessionStorage.clear());

  function setup(inputs: Record<string, unknown> = {}) {
    const username = new FormControl('', { nonNullable: true, validators: Validators.required });
    const password = new FormControl('', { nonNullable: true, validators: Validators.required });
    const fixture = TestBed.createComponent(LoginLayoutComponent);
    fixture.componentRef.setInput('username', username);
    fixture.componentRef.setInput('password', password);
    for (const [key, value] of Object.entries(inputs)) fixture.componentRef.setInput(key, value);
    const submitted = vi.fn();
    fixture.componentInstance.submitted.subscribe(submitted);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    return { fixture, el, username, password, submitted };
  }

  it('links labels to inputs and shows the portal name only when given', () => {
    const { el, fixture } = setup();
    const label = el.querySelector('label') as HTMLLabelElement;
    expect(el.querySelector(`#${label.htmlFor}`)).toBeInstanceOf(HTMLInputElement);
    expect(el.querySelector('h1')?.classList).toContain('sr-only');
    fixture.componentRef.setInput('portalName', 'Administration');
    fixture.detectChanges();
    expect(el.querySelector('h1.portal')?.textContent).toBe('Administration');
  });

  it('submits through the form and shows inline errors after touch', () => {
    const { el, fixture, username, submitted } = setup();
    username.markAsTouched();
    fixture.detectChanges();
    const input = el.querySelector('input') as HTMLInputElement;
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(el.querySelector(`#${input.getAttribute('aria-describedby')}`)?.textContent).toContain('This field is required');
    (el.querySelector('form') as HTMLFormElement).dispatchEvent(new Event('submit', { cancelable: true }));
    expect(submitted).toHaveBeenCalledTimes(1);
  });

  it('toggles password visibility and shows the busy state', () => {
    const { el, fixture, submitted } = setup();
    const toggle = el.querySelector('.toggle') as HTMLButtonElement;
    toggle.click();
    fixture.detectChanges();
    expect(el.querySelector<HTMLInputElement>('.has-toggle')?.type).toBe('text');
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    fixture.componentRef.setInput('busy', true);
    fixture.componentRef.setInput('error', 'Invalid username or password');
    fixture.detectChanges();
    const button = el.querySelector('.submit') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect(el.querySelector('[role="alert"]')?.textContent).toContain('Invalid username or password');
    (el.querySelector('form') as HTMLFormElement).dispatchEvent(new Event('submit', { cancelable: true }));
    expect(submitted).not.toHaveBeenCalled();
  });

  it('plays the splash reveal before the form once per session', () => {
    vi.useFakeTimers();
    try {
      const first = setup({ splash: true });
      expect(first.el.querySelector('form')).toBeNull();
      expect(first.el.querySelector('img')?.getAttribute('src')).toBe('assets/brand/e-sabong-logo-reveal.svg');
      vi.advanceTimersByTime(BRAND_REVEAL_MS);
      first.fixture.detectChanges();
      expect(first.el.querySelector('form')).not.toBeNull();

      const second = setup({ splash: true });
      expect(second.el.querySelector('form')).not.toBeNull();
      expect(second.el.querySelector('img')?.getAttribute('src')).toBe('assets/brand/e-sabong-logo.svg');
    } finally {
      vi.useRealTimers();
    }
  });
});

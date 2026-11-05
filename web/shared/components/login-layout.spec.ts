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

  it('uses the SWC mark for every variant and prefers a tenant logo', () => {
    for (const variant of ['static', 'compact', 'reveal', 'loop'] as const) {
      expect(logo(variant).getAttribute('src')).toBe('assets/brand/swc-logo.png');
    }
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

  it('links labels to inputs, names the workspace and shows the portal line', () => {
    const { el, fixture } = setup();
    const label = el.querySelector('label') as HTMLLabelElement;
    expect(el.querySelector(`#${label.htmlFor}`)).toBeInstanceOf(HTMLInputElement);
    expect(label.classList).toContain('sr-only'); // visible text is the placeholder; the label stays for assistive tech
    expect((el.querySelector(`#${label.htmlFor}`) as HTMLInputElement).placeholder).toBe('Username');
    expect(el.querySelector('h1.title')?.textContent).toBe('SWC MAIN');
    expect(el.querySelector('.portal')?.textContent).toBe('Online Gaming Portal');
    fixture.componentRef.setInput('portalName', 'Administration');
    fixture.detectChanges();
    expect(el.querySelector('.portal')?.textContent).toBe('Administration');
  });

  it('shows the jurisdiction disclaimer, Telegram button, and language flags', () => {
    const { el, fixture, submitted } = setup();
    expect(el.querySelector('.disclaimer')?.textContent).toContain('aged 21 and above');
    expect(el.querySelector('.disclaimer')?.textContent).toContain('jurisdiction');
    const telegram = el.querySelector('.telegram') as HTMLButtonElement;
    expect(telegram.textContent).toContain('Sign In with Telegram');
    expect(telegram.type).toBe('button');
    telegram.click();
    expect(submitted).not.toHaveBeenCalled();
    expect(el.querySelectorAll('.flag').length).toBe(4);
    expect(el.querySelector('.flag.active')?.getAttribute('aria-label')).toBe('English');
    const filipino = el.querySelector('[aria-label="Filipino"]') as HTMLButtonElement;
    filipino.click();
    fixture.detectChanges();
    expect(filipino.getAttribute('aria-pressed')).toBe('true');
    expect(el.querySelector('[aria-label="English"]')?.getAttribute('aria-pressed')).toBe('false');
    expect(el.querySelector('.extras')?.children.length).toBe(0);
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
      expect(first.el.querySelector('img')?.getAttribute('src')).toBe('assets/brand/swc-logo.png');
      vi.advanceTimersByTime(BRAND_REVEAL_MS);
      first.fixture.detectChanges();
      expect(first.el.querySelector('form')).not.toBeNull();

      const second = setup({ splash: true });
      expect(second.el.querySelector('form')).not.toBeNull();
      expect(second.el.querySelector('img')?.getAttribute('src')).toBe('assets/brand/swc-logo.png');
    } finally {
      vi.useRealTimers();
    }
  });
});

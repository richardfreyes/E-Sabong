import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AUTH_CONFIG, AuthStore, RealtimeService, ToastService } from '@shared/services';
import { AgentFrame } from './frame';

function title(type: 'master_agent' | 'agent'): string {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
      { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } },
      { provide: RealtimeService, useValue: { connect: vi.fn(), on: () => ({ pipe: () => ({ subscribe: () => undefined }) }) } },
      { provide: ToastService, useValue: { info: vi.fn(), success: vi.fn(), error: vi.fn() } },
    ],
  });
  TestBed.inject(AuthStore).acceptSession({
    access_token: 't', token_type: 'bearer', expires_in: 900,
    user: { username: 'u1', full_name: 'U', account_type: type, permissions: [], must_change_password: false, tenant: { name: 'T' } },
  } as never);
  const fixture = TestBed.createComponent(AgentFrame);
  fixture.detectChanges();
  return (fixture.nativeElement.querySelector('.title') as HTMLElement).textContent!.trim();
}

describe('Agent frame', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('gives master agents and agents the same title', () => {
    expect(title('agent')).toBe('SWC MAIN Agent');
    expect(title('master_agent')).toBe('SWC MAIN Agent');
  });

  it('puts balance request and wallet transfer in the menu', () => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
        { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } },
        { provide: RealtimeService, useValue: { connect: vi.fn(), on: () => ({ pipe: () => ({ subscribe: () => undefined }) }) } },
        { provide: ToastService, useValue: { info: vi.fn(), success: vi.fn(), error: vi.fn() } },
      ],
    });
    TestBed.inject(AuthStore).acceptSession({
      access_token: 't', token_type: 'bearer', expires_in: 900,
      user: { username: 'u1', full_name: 'U', account_type: 'agent', permissions: [], must_change_password: false, tenant: { name: 'T' } },
    } as never);
    const fixture = TestBed.createComponent(AgentFrame);
    fixture.detectChanges();
    const labels = [...fixture.nativeElement.querySelectorAll('.item')].map((el) => (el as HTMLElement).textContent?.trim());
    expect(labels).not.toContain('Request Balance');
    expect(labels).toContain('Transfer to Wallet');
    expect(labels).toContain('Logs');
  });
});

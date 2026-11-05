import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AUTH_CONFIG, AuthStore } from '@shared/services';
import { AgentFrame } from './frame';

function title(type: 'master_agent' | 'agent'): string {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } }],
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

  it('titles the agent dashboard SWC Main Agent and keeps SWC MAIN Agent for master agents', () => {
    expect(title('agent')).toBe('SWC Main Agent');
    expect(title('master_agent')).toBe('SWC MAIN Agent');
  });
});

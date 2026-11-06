import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';

import { AUTH_CONFIG, AgentUsersApi, RealtimeService, ToastService } from '@shared/services';
import { AgentInboxPage } from './inbox';

const row = {
  id: 'r1', player_id: 'p1', player_username: 'ply1', upline_id: 'a1', upline_username: 'agent1',
  kind: 'cashout' as const, amount: '8.00', status: 'pending' as const, created_at: '2026-11-06T04:00:00Z',
  decided_at: null, replayed: false,
};

function render(items = [row]) {
  const api = {
    inbox: vi.fn(() => of(items)),
    inboxCounts: vi.fn(() => of({ cashout: items.length, commission: 0 })),
    decide: vi.fn(() => of({ ...row, status: 'approved' as const })),
  };
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
      { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } },
      { provide: AgentUsersApi, useValue: api },
      { provide: RealtimeService, useValue: { connect: vi.fn(), on: () => of() } },
      { provide: ToastService, useValue: { success: vi.fn(), error: vi.fn(), info: vi.fn() } },
    ],
  });
  const fixture = TestBed.createComponent(AgentInboxPage);
  fixture.detectChanges();
  return { fixture, api, el: fixture.nativeElement as HTMLElement };
}

describe('My Inbox', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('lists a pending cashout and leaves commission empty', () => {
    const { el, fixture } = render();
    expect(el.querySelector('[data-tab="cashout"]')?.textContent).toContain('1');
    expect(el.querySelector('[data-tab="commission"]')?.textContent).toContain('0');
    expect(el.textContent).toContain('ply1');
    expect(el.textContent).toContain('8.00');
    (el.querySelector('[data-tab="commission"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(el.textContent).toContain('You have no pending requests.');
  });

  it('shows the empty state when nothing is waiting', () => {
    const { el } = render([]);
    expect(el.querySelector('.empty')?.textContent).toContain('You have no pending requests.');
  });

  it('approves a request', () => {
    const { el, api } = render();
    (el.querySelector('[data-action="approve-ply1"]') as HTMLButtonElement).click();
    expect(api.decide).toHaveBeenCalledWith('r1', 'approve');
  });
});

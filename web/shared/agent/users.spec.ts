import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';

import { AUTH_CONFIG, AgentUsersApi, AuthStore, CommissionApi, MyUserRow, RealtimeService, ToastService } from '@shared/services';
import { AgentUsersPage } from './users';

const agent: MyUserRow = { id: 'a1', code: 'A1B2C3D4', username: 'agent1', full_name: 'Agent One', account_type: 'agent', parent_username: 'boss', direct: true,
  status: 'active', online: true, via_referral: false, balance: null, earnings: '30.00', rate: '0.0300', created_at: '2026-11-06T01:00:00Z', last_login_at: null };
const player: MyUserRow = { id: 'p1', code: 'P1P1P1P1', username: 'ma1player1', full_name: 'player1', account_type: 'player', parent_username: 'boss', direct: true,
  status: 'suspended', online: false, via_referral: true, balance: '850.00', earnings: '8.50', rate: null, created_at: '2026-11-06T02:00:00Z', last_login_at: null };
const banned: MyUserRow = { ...player, id: 'p2', username: 'banned1', status: 'banned', via_referral: false };

function render(type: 'master_agent' | 'agent') {
  const api = {
    list: vi.fn(() => of({ items: [agent, player, banned], total: 3, page: 1, page_size: 15 })),
    summary: vi.fn(() => of({ players: { active: 1, blocked: 1, banned: 1, online: 0 }, agents: { active: 1, blocked: 0, banned: 0, online: 1 } })),
    block: vi.fn(() => of({})), unblock: vi.fn(() => of({})),
  };
  const commissions = { overview: vi.fn(() => of({ allocation: '0.0850', rate: '0.0850' })), setAgentRate: vi.fn(() => of({ ...agent, agent_rate: '0.0500', master_rate: '0.0350' })) };
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(), provideHttpClientTesting(), provideRouter([]), provideNoopAnimations(),
      { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } },
      { provide: AgentUsersApi, useValue: api }, { provide: CommissionApi, useValue: commissions },
      { provide: RealtimeService, useValue: { connect: vi.fn(), on: () => of() } },
      { provide: ToastService, useValue: { success: vi.fn(), error: vi.fn(), info: vi.fn() } },
    ],
  });
  TestBed.inject(AuthStore).acceptSession({ access_token: 't', token_type: 'bearer', expires_in: 900,
    user: { username: 'boss', account_type: type, permissions: ['users.view_downline'], must_change_password: false, tenant: { name: 'T' } } } as never);
  vi.useFakeTimers();
  const fixture = TestBed.createComponent(AgentUsersPage);
  fixture.detectChanges();
  vi.advanceTimersByTime(200);
  vi.useRealTimers();
  fixture.detectChanges();
  return { fixture, api, commissions, el: fixture.nativeElement as HTMLElement };
}

describe('My Users', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('shows status, online, banned, referral and rate columns with sidebar counts', () => {
    const { el } = render('master_agent');
    const row = (name: string) => el.querySelector(`[data-user="${name}"]`)!.textContent!;
    expect(row('agent1')).toContain('3.00%');
    expect(row('agent1')).toContain('Online');
    expect(row('ma1player1')).toContain('Blocked');
    expect(row('ma1player1')).toContain('Referral link');
    expect(row('ma1player1')).toContain('850.00');
    expect(row('banned1')).toContain('Banned');
    expect(el.querySelector('[data-filter="player-blocked"]')!.textContent).toContain('1');
    expect(el.querySelector('[data-filter="agent-online"]')!.textContent).toContain('1');
    expect(el.textContent).not.toContain('₱');
    expect(el.textContent).not.toContain('Add Balance');
  });

  it('filters by kind and state from the sidebar', () => {
    const { el, api, fixture } = render('master_agent');
    vi.useFakeTimers();
    (el.querySelector('[data-filter="player-banned"]') as HTMLButtonElement).click();
    vi.advanceTimersByTime(200);
    vi.useRealTimers();
    fixture.detectChanges();
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'player', state: 'banned', page: 1 }));
  });

  it('lets a master agent set a direct agent rate and shows what the master agent keeps', () => {
    const { fixture, commissions, el } = render('master_agent');
    const page = fixture.componentInstance;
    expect(page.canSetRate(agent)).toBe(true);
    expect(page.canSetRate(player)).toBe(false);
    page.editRate(agent);
    fixture.detectChanges();
    const input = el.querySelector('[data-field="rate-draft"]') as HTMLInputElement;
    input.value = '5'; input.dispatchEvent(new Event('input')); fixture.detectChanges();
    expect(el.querySelector('[data-field="keeps"]')!.textContent).toContain('3.50%');
    page.saveRate();
    expect(commissions.setAgentRate).toHaveBeenCalledWith('a1', '0.0500');
  });

  it('blocks and unblocks, but never touches a banned account', () => {
    const { fixture, api } = render('agent');
    const page = fixture.componentInstance;
    expect(page.master()).toBe(false);
    expect(page.canSetRate(agent)).toBe(false);
    page.toggleBlock(agent);
    expect(api.block).toHaveBeenCalledWith('a1');
    page.toggleBlock(player);
    expect(api.unblock).toHaveBeenCalledWith('p1');
    page.toggleBlock(banned);
    expect(api.block).toHaveBeenCalledTimes(1);
    expect(api.unblock).toHaveBeenCalledTimes(1);
  });
});

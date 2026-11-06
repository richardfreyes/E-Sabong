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
const active: MyUserRow = { ...player, id: 'p3', username: 'live1', status: 'active', balance: '10.00' };
const posted = {
  id: 't1', reference: 'XFR-1', sender_id: 'boss', sender_username: 'boss', sender_role: 'master_agent',
  player_id: 'p3', player_username: 'live1', amount: '10.00', sender_balance_before: '250.00', sender_balance_after: '240.00',
  player_balance_before: '10.00', player_balance_after: '20.00', status: 'applied', created_at: '2026-11-06T03:00:00Z', replayed: false,
};

function render(type: 'master_agent' | 'agent') {
  const api = {
    list: vi.fn(() => of({ items: [agent, player, banned, active], total: 4, page: 1, page_size: 15 })),
    summary: vi.fn(() => of({ players: { active: 1, blocked: 1, banned: 1, online: 0 }, agents: type === 'master_agent' ? { active: 1, blocked: 0, banned: 0, online: 1 } : null })),
    block: vi.fn(() => of({})), unblock: vi.fn(() => of({})),
    wallet: vi.fn(() => of({ balance: '250.00', version: 1 })),
    transfers: vi.fn(() => of({ items: [posted], total: 1, page: 1, page_size: 8 })),
    preview: vi.fn(() => of({ player_id: 'p3', player_username: 'live1', player_balance: '10.00', sender_balance: '250.00' })),
    transfer: vi.fn(() => of(posted)),
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
    expect(el.querySelector('[data-action="add-balance-live1"]')).toBeTruthy();
    expect(el.querySelector('[data-action="add-balance-agent1"]')).toBeTruthy();
    expect(el.querySelector('[data-action="add-balance-ma1player1"]')).toBeNull();
    expect(el.querySelector('[data-testid="transfer-history"]')?.textContent).toContain('XFR-1');
  });

  it('confirms a wallet transfer and updates both balances', () => {
    const { fixture, api, el } = render('master_agent');
    (el.querySelector('[data-action="add-balance-live1"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(el.querySelector('[aria-labelledby="transfer-title"], #transfer-title')).toBeTruthy();
    expect(el.querySelector('[data-field="sender-wallet"]')?.textContent).toContain('250.00');
    const input = el.querySelector('[data-field="transfer-amount"]') as HTMLInputElement;
    input.value = '10';
    input.dispatchEvent(new Event('input'));
    (el.querySelector('[data-action="confirm-transfer"]') as HTMLButtonElement).click();
    expect(api.transfer).toHaveBeenCalledWith('p3', { amount: '10.00', idempotency_key: expect.stringMatching(/^transfer-/) });
    fixture.detectChanges();
    expect(el.querySelector('[data-user="live1"]')?.textContent).toContain('20.00');
    expect(el.querySelector('#transfer-title')).toBeNull();
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
    expect(page.canFund(agent)).toBe(false);
    expect(page.canFund(active)).toBe(true);
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

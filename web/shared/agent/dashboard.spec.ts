import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import type { Type } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { MatDialog } from '@angular/material/dialog';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import type { AdminDashboard, BalanceSummary } from '@shared/models';
import { AUTH_CONFIG, AdminApi, AuthStore, CommissionApi, CommissionOverview, PortalApi, RealtimeService, ToastService } from '@shared/services';
import { AgentCommissionPage } from './commission-page';
import { AgentDashboardPage } from './dashboard';
import { agentShareLinks } from './links';

const summary: BalanceSummary = {
  accounts: 4, participants: 3, total_balance: '1500.50', pending_corrections: null,
  reset_policy: 'disabled', starting_balance: '1000.00', can_manage: false, disclaimer: 'play',
};
const dash: AdminDashboard = {
  users_by_type: { agent: 2, player: 3 }, active_users_7d: null, events_by_status: null, matches_in_progress: null,
  results_7d: null, points: null, activity: null, recent_audit: null, modules: [],
};

const rates = {
  enabled: true, draw_multiplier: '8', commission_rate: '0.1000', min_bet: '10', max_bet: '5000', max_fight_stake: '100000', wallet: null, disclaimer: 'play',
} as never;

const masterEarnings: CommissionOverview = {
  role: 'master_agent', rate: '0.0850', allocation: '0.0850', upline_rate: null, total_earned: '98.50', this_month: '98.50',
  last_month: '12.00', network_total: '178.50', network_this_month: '178.50', network_last_month: '20.00',
  available_earnings: '98.50', own_balance: '250.00',
};
const agentEarnings: CommissionOverview = {
  role: 'agent', rate: '0.0300', allocation: null, upline_rate: null, total_earned: '30.00', this_month: '30.00',
  last_month: '0.00', network_total: '30.00', network_this_month: '30.00', network_last_month: '0.00',
  available_earnings: '30.00', own_balance: '0.00',
};
const agentRow = { agent_id: 'a1', username: 'agent1', full_name: 'Agent One', status: 'active', players: 1,
  agent_rate: '0.0300', master_rate: '0.0550', allocation: '0.0850', agent_earned: '30.00', master_earned: '55.00' };
const historyRow = { id: 'h1', created_at: '2026-11-06T02:00:00Z', match_id: 'm1', match_number: 7, event_name: 'Derby', player_id: 'p1',
  player_username: 'ply1', agent_id: 'a1', agent_username: 'agent1', side: 'meron', bet_amount: '1000.00', agent_rate: '0.0300',
  agent_points: '30.00', master_rate: '0.0550', master_points: '55.00', outcome: 'meron', result_revision: 1, status: 'earned' as const };

function commissionApi(type: 'master_agent' | 'agent', overrides: Partial<CommissionApi> = {}) {
  return {
    overview: vi.fn(() => of(type === 'master_agent' ? masterEarnings : agentEarnings)),
    approvers: vi.fn(() => of([])),
    balanceRequests: vi.fn(() => of([])),
    earningsMoves: vi.fn(() => of([])),
    agents: vi.fn(() => of([agentRow])),
    setAgentRate: vi.fn((_id: string, rate: string) => of({ ...agentRow, agent_rate: rate, master_rate: (0.085 - Number(rate)).toFixed(4) })),
    history: vi.fn(() => of({ items: [type === 'master_agent' ? historyRow : { ...historyRow, master_rate: null, master_points: null }], total: 1, page: 1, page_size: 10 })),
    breakdown: vi.fn(() => of([{ key: 'p1', label: 'ply1', bets: 1, eligible_points: '1000.00', agent_points: '30.00', master_points: type === 'master_agent' ? '55.00' : null }])),
    ...overrides,
  };
}

function render<T = AgentDashboardPage>(api: Partial<AdminApi>, type: 'master_agent' | 'agent' = 'master_agent', commissions = commissionApi(type),
  permissions = ['points.view_downline', 'dashboard.view'], component: Type<T> = AgentDashboardPage as unknown as Type<T>) {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
      { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } },
      { provide: ToastService, useValue: { info: () => undefined, error: () => undefined, success: () => undefined } },
      { provide: PortalApi, useValue: { betting: () => of(rates) } },
      { provide: CommissionApi, useValue: commissions },
      { provide: RealtimeService, useValue: { connect: vi.fn(), on: () => of() } },
      { provide: MatDialog, useValue: { open: () => ({ afterClosed: () => of(undefined) }) } },
      { provide: AdminApi, useValue: { balanceSummary: () => of(summary), dashboard: () => of(dash), balanceEntries: () => of({ items: [], total: 0, page: 1, page_size: 8 }), ...api } },
    ],
  });
  TestBed.inject(AuthStore).acceptSession({
    access_token: 't', token_type: 'bearer', expires_in: 900,
    user: { username: 'master1', full_name: 'Master Agent One', account_type: type, permissions, must_change_password: false, tenant: { name: 'T' } },
  } as never);
  vi.useFakeTimers();
  const fixture = TestBed.createComponent(component);
  fixture.detectChanges();
  vi.advanceTimersByTime(200); // commission loads are debounced
  vi.useRealTimers();
  fixture.detectChanges();
  return fixture;
}

describe('Agent dashboard', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('builds a unique player link and an agent application link', () => {
    expect(agentShareLinks('https://arena.example', 'Master 1')).toEqual({
      player: 'https://arena.example/register?ref=Master%201',
      application: 'https://arena.example/register?ref=Master%201&apply=agent',
    });
  });

  it('shows the main wallet and the recorded commission as earnings', () => {
    const fixture = render({});
    const el = fixture.nativeElement as HTMLElement;
    const text = el.textContent as string;
    // Total Balance = Main Wallet (own play balance 250.00) + available earnings (98.50).
    expect(el.querySelector('[data-field="total-balance"]')?.textContent).toContain('348.50');
    expect(el.querySelector('.wallet .sheet-value')?.textContent?.replace(/\s/g, '')).toContain('₱250.00');
    expect(el.querySelector('.wallet .loading')?.textContent).toContain('Loading form');
    expect(el.textContent).not.toContain('Balance transfers');
    // Earnings stay on their own card until Convert earnings merges them into the Main Wallet.
    expect(el.querySelector('.earn .sheet-value')?.textContent?.replace(/\s/g, '')).toBe('₱98.50');
    expect(el.querySelector('.earn .cashout')?.textContent).toContain('Cashout form');
    expect(el.querySelector('[data-field="period-earned"]')?.textContent).toContain('98.50');
    expect(el.querySelector('[data-field="network-earned"]')?.textContent).toContain('178.50');
    expect(el.querySelector('[data-field="my-rate"]')?.textContent).toContain('8.50%');
    expect(text).toContain('Master Agent');
    expect(text).toContain('Welcome Back, Master Agent One!');
    expect(text).toContain('Current Month');
    expect(el.querySelector('.wallet .sheet-value')?.textContent).toContain('₱');
    expect(el.querySelector('.earn .sheet-value')?.textContent).toContain('₱');
    // Live Games only: no Casino Games, Lotteries or Exclusives cards.
    expect(el.querySelectorAll('.cat')).toHaveLength(3);
    expect(el.textContent).not.toContain('Casino Games');
    expect(el.textContent).not.toContain('Lotteries');
    expect(el.textContent).not.toContain('Exclusives');
    expect(el.querySelector('#player-link')).toBeTruthy();
    expect(el.querySelector('#agent-link')).toBeTruthy();
  });

  it('copies the referral link', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const fixture = render({});
    fixture.nativeElement.querySelector('[aria-label="Copy player referral link"]').click();
    await fixture.whenStable();
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('/register?ref=master1'));
  });

  it('shows an error and a retry when the main wallet cannot be loaded', () => {
    const fixture = render({}, 'master_agent', commissionApi('master_agent', {
      overview: () => throwError(() => ({ error: { error: { message: 'Balance information is not available' } } })),
    }));
    const text = (fixture.nativeElement as HTMLElement).textContent as string;
    expect(text).toContain('Try again');
    expect(text).not.toContain('1,500.50');
  });

  it('opens guide panels with the configured rates and keeps casino nonfunctional', () => {
    const fixture = render({}, 'agent');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.role')?.textContent).toContain('Agent');
    expect(el.querySelector('.role')?.textContent).not.toContain('Master');
    expect((el.querySelector('.switch-row input') as HTMLInputElement).disabled).toBe(true);
    fixture.componentInstance.toggle('rates');
    fixture.detectChanges();
    expect(el.querySelector('#body-rates')?.textContent).toContain('10.00%');
    expect(el.querySelector('#body-rates')?.textContent).toContain('3.00%');
    fixture.componentInstance.toggle('no-commission');
    fixture.detectChanges();
    expect(el.querySelector('#body-rates')).toBeNull();
    expect(el.querySelector('#body-no-commission')?.textContent).toContain('Coming Soon');
  });

  it('keeps the commission ledger off the dashboard and links to the Commission page', () => {
    const el = render({}).nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="commission"]')).toBeNull();
    expect(el.querySelector('[data-testid="player-commission"]')).toBeNull();
  });
});

describe('Agent Commission page', () => {
  afterEach(() => TestBed.resetTestingModule());
  const page = (type: 'master_agent' | 'agent', commissions = commissionApi(type)) =>
    render<AgentCommissionPage>({}, type, commissions, undefined, AgentCommissionPage);

  it('lets a master agent set an agent rate and shows what the master agent keeps', async () => {
    const commissions = commissionApi('master_agent');
    const fixture = page('master_agent', commissions);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('#commission-title')?.textContent?.trim()).toContain('Commission');
    expect(el.querySelector('[data-field="my-commission-rate"]')?.textContent).toContain('8.50%');
    const row = el.querySelector('[data-agent="agent1"]') as HTMLElement;
    expect(row.querySelector('[data-field="master-rate"]')?.textContent).toContain('5.50%');
    const input = row.querySelector('[data-field="rate-input"]') as HTMLInputElement;
    input.value = '5'; input.dispatchEvent(new Event('input')); fixture.detectChanges();
    expect(row.querySelector('[data-field="master-rate"]')?.textContent).toContain('3.50%');
    (row.querySelector('[data-field="save-rate"]') as HTMLButtonElement).click();
    expect(commissions.setAgentRate).toHaveBeenCalledWith('a1', '0.0500');
    // A rate above the allocation is refused before it reaches the server.
    input.value = '9'; input.dispatchEvent(new Event('input')); fixture.detectChanges();
    expect(row.querySelector('[data-field="master-rate"]')?.textContent).toContain('—');
    await fixture.componentInstance.saveRate(fixture.componentInstance.agentRows()![0]);
    expect(commissions.setAgentRate).toHaveBeenCalledTimes(1);
    const history = el.querySelector('[data-testid="commission-history"]') as HTMLElement;
    expect(history.textContent).toContain('ply1');
    expect(history.textContent).toContain('55.00');
  });

  it('shows an agent their own rate and earnings, read-only, without the master agent share', () => {
    const commissions = commissionApi('agent');
    const fixture = page('agent', commissions);
    const el = fixture.nativeElement as HTMLElement;
    // Same screen as the master agent's: same title, stats and columns.
    expect(el.querySelector('#commission-title')?.textContent).toContain('Commission');
    expect(el.querySelector('[data-field="my-commission-rate"]')?.textContent).toContain('3.00%');
    expect(el.querySelector('[data-field="my-share"]')?.textContent).toContain('30.00');
    expect(el.querySelector('[data-testid="agent-rates"]')).toBeNull();
    expect(el.querySelector('[data-field="rate-input"]')).toBeNull();
    expect(commissions.agents).not.toHaveBeenCalled();
    const history = el.querySelector('[data-testid="commission-history"]') as HTMLElement;
    expect(history.textContent).toContain('30.00');
    expect(history.textContent).not.toContain('55.00');
  });

  it('lists commission from every player: bet amount, percent and total earnings', () => {
    const fixture = page('master_agent');
    const el = fixture.nativeElement as HTMLElement;
    const row = el.querySelector('[data-testid="player-commission"] [data-player="ply1"]') as HTMLElement;
    expect(row.textContent).toContain('1,000.00');
    // Master agent's own share: 55 of 1,000 = 5.50%.
    expect(row.querySelector('[data-field="percent"]')?.textContent).toContain('5.50%');
    expect(row.querySelector('[data-field="earned"]')?.textContent).toContain('+55.00');
    TestBed.resetTestingModule();
    const agentView = page('agent').nativeElement as HTMLElement;
    const own = agentView.querySelector('[data-testid="player-commission"] [data-player="ply1"]') as HTMLElement;
    expect(own.querySelector('[data-field="percent"]')?.textContent).toContain('3.00%');
    expect(own.querySelector('[data-field="earned"]')?.textContent).toContain('+30.00');
  });
});

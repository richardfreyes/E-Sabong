import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import type { AdminDashboard, BalanceSummary } from '@shared/models';
import { AUTH_CONFIG, AdminApi, AuthStore, PortalApi, ToastService } from '@shared/services';
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

function render(api: Partial<AdminApi>, type: 'master_agent' | 'agent' = 'master_agent') {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
      { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login' } },
      { provide: ToastService, useValue: { info: () => undefined, error: () => undefined, success: () => undefined } },
      { provide: PortalApi, useValue: { betting: () => of(rates) } },
      { provide: AdminApi, useValue: { balanceSummary: () => of(summary), dashboard: () => of(dash), balanceEntries: () => of({ items: [], total: 0, page: 1, page_size: 8 }), ...api } },
    ],
  });
  TestBed.inject(AuthStore).acceptSession({
    access_token: 't', token_type: 'bearer', expires_in: 900,
    user: { username: 'master1', full_name: 'Master Agent One', account_type: type, permissions: ['points.view_downline', 'dashboard.view'], must_change_password: false, tenant: { name: 'T' } },
  } as never);
  const fixture = TestBed.createComponent(AgentDashboardPage);
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

  it('shows the network play balance in the reference layout without inventing earnings', () => {
    const fixture = render({});
    const el = fixture.nativeElement as HTMLElement;
    const text = el.textContent as string;
    expect(el.querySelector('.total-value')?.textContent).toContain('1,500.50');
    expect(el.querySelector('.wallet .sheet-value')?.textContent).toContain('1,500.50');
    expect(el.querySelector('.earn .sheet-value')?.textContent?.trim()).toBe('—');
    expect(text).toContain('Master Agent');
    expect(text).toContain('Welcome Back, Master Agent One!');
    expect(text).toContain('Current Month');
    expect(text).not.toContain('₱');
    expect(el.querySelectorAll('.cat')).toHaveLength(6);
    expect(el.querySelectorAll('.cat .cat-value.soon')).toHaveLength(3);
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

  it('shows an error and a retry when balances cannot be loaded', () => {
    const text = render({ balanceSummary: () => throwError(() => ({ error: { error: { message: 'Balance information is not available' } } })) }).nativeElement.textContent as string;
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
    expect(el.querySelector('#body-rates')?.textContent).toContain('Not configured');
    fixture.componentInstance.toggle('no-commission');
    fixture.detectChanges();
    expect(el.querySelector('#body-rates')).toBeNull();
    expect(el.querySelector('#body-no-commission')?.textContent).toContain('₱0.00');
  });
});

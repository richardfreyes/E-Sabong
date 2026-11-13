import { DatePipe, PercentPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { MatDialog } from '@angular/material/dialog';
import { Observable, Subject, catchError, debounceTime, firstValueFrom, forkJoin, map, of, switchMap } from 'rxjs';

import type { AdminDashboard, BalanceSummary, BettingConfig, PlayEntryKind, PlayWalletEntry, TenantProfile } from '@shared/models';
import { ArenaPointsPipe } from '@shared/pipes';
import {
  AdminApi, AuthStore, CommissionApi, CommissionOverview, PortalApi, RealtimeService, ToastService, toApiError,
} from '@shared/services';

import { AGENT_PORTAL } from './access';
import { CashoutDialog } from './cashout-dialog';
import { LoadingDialog } from './loading-dialog';
import { agentShareLinks } from './links';

const ENTRY_LABEL: Record<PlayEntryKind, string> = {
  grant: 'Starting balance', stake: 'Bet placed', payout: 'Win credited', refund: 'Refund', resettlement: 'Result correction',
  account_grant: 'Account grant', uniform_grant: 'Uniform grant', reset: 'Reset', correction: 'Balance correction',
  transfer: 'Balance transfer', earnings: 'Earnings moved to play balance',
};

/** Agent Guide panels. Each id is also a URL fragment the agent menu links to. */
export type GuidePanel = 'wallets' | 'rates' | 'commissioned' | 'no-commission' | 'earnings';
const PANELS: readonly GuidePanel[] = ['wallets', 'rates', 'commissioned', 'no-commission', 'earnings'];

type Loaded<T> = { ok: true; value: T } | { ok: false; message: string };

function pack<T>(source: Observable<T>): Observable<Loaded<T>> {
  return source.pipe(
    map((value) => ({ ok: true as const, value })),
    catchError((err: unknown) => of({ ok: false as const, message: toApiError(err).message })),
  );
}

/**
 * Master agent and agent home, recreated from the SWC MAIN agent reference.
 * Every figure comes from the API, scoped to the caller's tenant. Earnings are commission from
 * official MERON/WALA results. A master agent or agent can ask a Loader or administrator for play
 * balance, and can move their own available earnings into their own play wallet.
 */
@Component({
  selector: 'app-agent-dashboard',
  imports: [MatIconModule, MatMenuModule, RouterLink, ArenaPointsPipe, DatePipe, PercentPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './dashboard.scss',
  templateUrl: './dashboard.html',
})
export class AgentDashboardPage {
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly portal = inject(AGENT_PORTAL);
  private readonly admin = inject(AdminApi);
  private readonly portalApi = inject(PortalApi);
  private readonly commissionApi = inject(CommissionApi);
  private readonly dialog = inject(MatDialog);
  private readonly realtime = inject(RealtimeService);
  private readonly commission$ = new Subject<void>();
  private readonly toast = inject(ToastService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly load$ = new Subject<void>();
  private copyTimer: ReturnType<typeof setTimeout> | null = null;

  readonly summary = signal<BalanceSummary | null>(null);
  readonly dash = signal<AdminDashboard | null>(null);
  readonly rates = signal<BettingConfig | null>(null);
  readonly entries = signal<PlayWalletEntry[] | null>(null);
  readonly summaryError = signal<string | null>(null);
  readonly ratesError = signal<string | null>(null);
  readonly entriesError = signal<string | null>(null);
  readonly copied = signal<'player' | 'application' | null>(null);
  readonly panel = signal<GuidePanel | null>(null);
  readonly period = signal<'current' | 'last'>('current');

  // ---- commission (earnings) ----
  readonly earnings = signal<CommissionOverview | null>(null);
  readonly earningsError = signal<string | null>(null);
  /** Main Wallet: this account's own play balance. Convert earnings merges into it, and cashout takes it down. */
  readonly walletBalance = computed<string | null>(() => this.earnings()?.own_balance ?? null);
  /** Total Balance = Main Wallet + available earnings. */
  readonly totalBalance = computed<number | null>(() => {
    const wallet = this.walletBalance();
    const earned = this.earnings()?.available_earnings;
    if (wallet === null || earned === undefined) return null;
    return Math.round((Number(wallet) + Number(earned)) * 100) / 100;
  });

  /** Earned points for the selected period (this month or last month), and the comparison period. */
  readonly periodEarned = computed(() => {
    const e = this.earnings();
    if (!e) return null;
    return this.period() === 'current'
      ? { mine: e.this_month, compare: e.last_month, network: e.network_this_month, networkCompare: e.network_last_month }
      : { mine: e.last_month, compare: null, network: e.network_last_month, networkCompare: null };
  });

  readonly master = computed(() => this.auth.user()?.account_type === 'master_agent');
  readonly roleLabel = computed(() => (this.master() ? 'Master Agent' : this.auth.user()?.account_type === 'sub_agent' ? 'Sub-Agent' : 'Agent'));
  readonly displayName = computed(() => this.auth.user()?.full_name || this.auth.user()?.username || '');
  readonly handle = computed(() => this.auth.user()?.username ?? '');
  readonly links = computed(() => agentShareLinks(globalThis.location?.origin ?? '', this.handle(), this.auth.user()?.account_type));
  readonly periodLabel = computed(() => (this.period() === 'current' ? 'Current Month' : 'Last Month'));
  readonly compareLabel = computed(() => (this.period() === 'current' ? 'Last Month' : 'Month Before'));
  /** Agents below a master agent. The API counts the caller too when it is itself an agent. */
  readonly agentCount = computed(() => {
    const byType = this.dash()?.users_by_type;
    if (!byType) return null;
    const agents = byType.agent ?? 0;
    return this.master() ? agents : this.auth.user()?.account_type === 'agent' ? (byType.sub_agent ?? 0) : 0;
  });
  readonly liveNow = computed(() => this.dash()?.matches_in_progress ?? null);
  readonly houseRate = computed(() => { const r = this.rates(); return r ? Number(r.commission_rate) : null; });
  readonly canSeeNetwork = computed(() => this.auth.hasPermission('points.view_downline'));
  readonly commissionRoute = computed(() => (this.portal === 'player' ? '/agent/commission' : '/commission'));

  constructor() {
    inject(DestroyRef).onDestroy(() => { if (this.copyTimer) clearTimeout(this.copyTimer); });
    this.load$.pipe(
      switchMap(() => forkJoin({
        summary: pack(this.admin.balanceSummary()),
        dash: pack(this.admin.dashboard()),
        rates: pack(this.portalApi.betting()),
        entries: pack(this.admin.balanceEntries({ page: 1, page_size: 8 })),
      })),
      takeUntilDestroyed(),
    ).subscribe(({ summary, dash, rates, entries }) => {
      if (summary.ok) this.summary.set(summary.value); else this.summaryError.set(summary.message);
      if (dash.ok) this.dash.set(dash.value);
      if (rates.ok) this.rates.set(rates.value); else this.ratesError.set(rates.message);
      if (entries.ok) this.entries.set(entries.value.items); else this.entriesError.set(entries.message);
    });
    this.load();

    // Earnings overview for the cards; the full ledger lives on the Commission page.
    this.commission$.pipe(
      debounceTime(150),
      switchMap(() => pack(this.commissionApi.overview())),
      takeUntilDestroyed(),
    ).subscribe((overview) => {
      if (overview.ok) { this.earnings.set(overview.value); this.earningsError.set(null); } else this.earningsError.set(overview.message);
    });
    this.reloadCommission();

    // Earnings follow official results live: the server tells this account when its ledger moved.
    this.realtime.connect();
    this.realtime.on('users.updated').pipe(takeUntilDestroyed()).subscribe(() => this.load());
    this.realtime.on('commission.updated').pipe(takeUntilDestroyed()).subscribe(() => this.reloadCommission());
    this.realtime.on<{ purpose: string; status: string }>('balance.updated').pipe(takeUntilDestroyed()).subscribe((event) => {
      this.reloadCommission();
      const decided = event?.status === 'approved' ? 'approved' : event?.status === 'rejected' ? 'declined' : '';
      if (!decided) return;
      const what = event.purpose === 'cashout' ? 'Cashout' : event.purpose === 'earnings' ? 'Earnings withdrawal' : 'Balance request';
      this.toast.info(`${what} ${decided}. Your balance is updated.`);
    });

    // Agent menu links (#wallets, #rates, ...) open the matching guide panel.
    inject(ActivatedRoute).fragment.pipe(takeUntilDestroyed()).subscribe((fragment) => this.reveal(fragment));
  }

  load(): void {
    this.summaryError.set(null);
    this.ratesError.set(null);
    this.entriesError.set(null);
    this.summary.set(null);
    this.load$.next();
  }

  reveal(fragment: string | null): void {
    if (!fragment) return;
    if ((PANELS as readonly string[]).includes(fragment)) this.panel.set(fragment as GuidePanel);
    else if (fragment !== 'guide') return;
    setTimeout(() => {
      const target = this.host.nativeElement.querySelector<HTMLElement>(fragment === 'guide' ? '#guide' : `#panel-${fragment}`);
      target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      target?.querySelector<HTMLElement>('button')?.focus({ preventScroll: true });
    });
  }

  open(id: GuidePanel): void {
    this.reveal(id);
  }

  toggle(id: GuidePanel): void {
    this.panel.update((current) => (current === id ? null : id));
  }

  async copy(kind: 'player' | 'application'): Promise<void> {
    const text = kind === 'player' ? this.links().player : this.links().application;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(text);
      this.copied.set(kind);
      this.toast.success(kind === 'player' ? 'Player referral link copied' : 'Agent application link copied');
      if (this.copyTimer) clearTimeout(this.copyTimer);
      this.copyTimer = setTimeout(() => this.copied.set(null), 2000);
    } catch {
      this.toast.error('Could not copy the link. Select it and copy it manually.');
    }
  }

  reloadCommission(): void {
    this.commission$.next();
  }

  /** "0.0550" -> "5.50%". */
  pct(rate: string | null | undefined): string {
    return rate === null || rate === undefined ? '—' : `${(Number(rate) * 100).toFixed(2)}%`;
  }

  entryLabel(kind: PlayEntryKind): string {
    return ENTRY_LABEL[kind] ?? kind;
  }

  async openCashout(): Promise<void> {
    const ref = this.dialog.open(CashoutDialog, {
      data: {
        available: this.earnings()?.available_earnings ?? '0.00',
        ownBalance: this.earnings()?.own_balance ?? '0.00',
      },
      width: '460px',
      maxWidth: '95vw',
      autoFocus: 'dialog',
    });
    const result = await firstValueFrom(ref.afterClosed());
    if (result === 'moved' || result === 'requested') {
      this.reloadCommission();
      this.load();
    }
  }

  async openLoading(): Promise<void> {
    const ref = this.dialog.open(LoadingDialog, {
      data: { balance: this.walletBalance() ?? this.earnings()?.own_balance ?? '0.00' },
      width: '460px',
      maxWidth: '95vw',
      autoFocus: 'dialog',
    });
    if (await firstValueFrom(ref.afterClosed())) {
      this.reloadCommission();
      this.load();
    }
  }
}

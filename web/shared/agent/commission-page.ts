import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';
import { Observable, Subject, catchError, debounceTime, forkJoin, map, of, switchMap } from 'rxjs';

import type { BalanceSummary, TenantProfile } from '@shared/models';
import { ArenaPointsPipe } from '@shared/pipes';
import {
  AdminApi, AgentCommissionRow, AuthStore, CommissionApi, CommissionBreakdownRow, CommissionHistoryRow, CommissionOverview, RealtimeService,
  ToastService, toApiError,
} from '@shared/services';

type Loaded<T> = { ok: true; value: T } | { ok: false; message: string };

function pack<T>(source: Observable<T>): Observable<Loaded<T>> {
  return source.pipe(
    map((value) => ({ ok: true as const, value })),
    catchError((err: unknown) => of({ ok: false as const, message: toApiError(err).message })),
  );
}

/**
 * Commission page of the agent workspace (master agents and agents alike): rate and totals,
 * commission from every player, a master agent's agent rates, the ledger and the breakdown.
 * Every figure comes from ``/commissions``, scoped to the caller. Earnings never touch a wallet.
 */
@Component({
  selector: 'app-agent-commission',
  imports: [MatIconModule, ArenaPointsPipe, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrls: ['./dashboard.scss', './commission.scss'],
  templateUrl: './commission-page.html',
})
export class AgentCommissionPage {
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  private readonly admin = inject(AdminApi);
  private readonly commissionApi = inject(CommissionApi);
  private readonly realtime = inject(RealtimeService);
  private readonly toast = inject(ToastService);
  private readonly commission$ = new Subject<void>();

  readonly subAgent = computed(() => this.auth.user()?.account_type === 'sub_agent');
  readonly managesRates = computed(() => ['master_agent','agent'].includes(this.auth.user()?.account_type ?? ''));
  readonly master = computed(() => this.auth.user()?.account_type === 'master_agent');
  readonly summary = signal<BalanceSummary | null>(null);
  readonly earnings = signal<CommissionOverview | null>(null);
  readonly earningsError = signal<string | null>(null);
  readonly agentRows = signal<AgentCommissionRow[] | null>(null);
  readonly rateDrafts = signal<Record<string, string>>({});
  readonly savingRate = signal<string | null>(null);
  readonly history = signal<CommissionHistoryRow[] | null>(null);
  readonly historyTotal = signal(0);
  readonly historyPage = signal(1);
  readonly historyAgent = signal<string | null>(null);
  readonly breakdownBy = signal<'player' | 'fight'>('player');
  readonly breakdown = signal<CommissionBreakdownRow[] | null>(null);
  /** Commission from every player (all fights, net of reversals), paged on the client. */
  readonly playerCommission = signal<CommissionBreakdownRow[] | null>(null);
  readonly playerPage = signal(1);
  readonly playerPageSize = signal(15);
  readonly playerPageSizes = [15, 25, 50];
  readonly playerPages = computed(() => Math.max(1, Math.ceil((this.playerCommission()?.length ?? 0) / this.playerPageSize())));
  readonly playerRows = computed(() => {
    const rows = this.playerCommission() ?? [];
    const start = (this.playerPage() - 1) * this.playerPageSize();
    return rows.slice(start, start + this.playerPageSize());
  });
  readonly playerRange = computed(() => {
    const total = this.playerCommission()?.length ?? 0;
    if (!total) return '0 of 0';
    const start = (this.playerPage() - 1) * this.playerPageSize() + 1;
    return `${start}–${Math.min(total, start + this.playerPageSize() - 1)} of ${total}`;
  });
  readonly historyPageSize = 10;
  readonly historyPages = computed(() => Math.max(1, Math.ceil(this.historyTotal() / this.historyPageSize)));

  constructor() {
    this.commission$.pipe(
      debounceTime(150),
      switchMap(() => forkJoin({
        overview: pack(this.commissionApi.overview()),
        // Only a master agent has agents to set rates for; everyone else gets the same page without that table.
        agents: this.managesRates() ? pack(this.commissionApi.agents()) : of(null),
        history: pack(this.commissionApi.history({ page: this.historyPage(), page_size: this.historyPageSize, agent_id: this.historyAgent() })),
        breakdown: pack(this.commissionApi.breakdown(this.breakdownBy(), this.historyAgent())),
        players: pack(this.commissionApi.breakdown('player')),
      })),
      takeUntilDestroyed(),
    ).subscribe(({ overview, agents, history, breakdown, players }) => {
      if (overview.ok) { this.earnings.set(overview.value); this.earningsError.set(null); } else this.earningsError.set(overview.message);
      if (agents?.ok) this.agentRows.set(agents.value);
      if (history.ok) { this.history.set(history.value.items); this.historyTotal.set(history.value.total); }
      if (breakdown.ok) this.breakdown.set(breakdown.value);
      if (players.ok) {
        this.playerCommission.set(players.value);
        this.playerPage.update((page) => Math.min(page, this.playerPages()));
      }
    });
    this.reloadCommission();
    this.admin.balanceSummary().pipe(takeUntilDestroyed()).subscribe({ next: (s) => this.summary.set(s), error: () => undefined });
    // The ledger follows official results live.
    this.realtime.connect();
    this.realtime.on('commission.updated').pipe(takeUntilDestroyed()).subscribe(() => this.reloadCommission());
  }

  reloadCommission(): void {
    this.commission$.next();
  }

  /** The caller's own share of a ledger row (master agents and agents read the same columns). */
  myShare(row: CommissionHistoryRow): string {
    return this.master() ? (row.master_points ?? '0') : this.subAgent() ? (row.sub_agent_points ?? '0') : row.agent_points;
  }

  myRate(row: CommissionHistoryRow): string | null {
    return this.master() ? row.master_rate : this.subAgent() ? (row.sub_agent_rate ?? '0') : row.agent_rate;
  }

  /** The caller's own points from a player row, and that as a percentage of the player's eligible bets. */
  playerEarned(row: CommissionBreakdownRow): string {
    return this.master() ? (row.master_points ?? '0') : this.subAgent() ? (row.sub_agent_points ?? '0') : row.agent_points;
  }

  playerPercent(row: CommissionBreakdownRow): string {
    const bet = Number(row.eligible_points);
    return bet > 0 ? `${((Number(this.playerEarned(row)) / bet) * 100).toFixed(2)}%` : '—';
  }

  playersGoTo(page: number): void {
    this.playerPage.set(Math.min(Math.max(1, page), this.playerPages()));
  }

  setPlayerPageSize(size: string): void {
    this.playerPageSize.set(Number(size));
    this.playerPage.set(1);
  }

  /** "0.0550" -> "5.50%". */
  pct(rate: string | null | undefined): string {
    return rate === null || rate === undefined ? '—' : `${(Number(rate) * 100).toFixed(2)}%`;
  }

  rateDraft(row: AgentCommissionRow): string {
    return this.rateDrafts()[row.agent_id] ?? (Number(row.agent_rate) * 100).toFixed(2);
  }

  editRate(row: AgentCommissionRow, value: string): void {
    this.rateDrafts.update((d) => ({ ...d, [row.agent_id]: value.replace(/[^\d.]/g, '') }));
  }

  /** What the master agent keeps if the draft rate is saved, or null for an invalid draft. */
  remainder(row: AgentCommissionRow): number | null {
    const draft = Number(this.rateDraft(row));
    const total = Number(row.allocation) * 100;
    return /^\d+(\.\d{1,2})?$/.test(this.rateDraft(row)) && Number.isFinite(draft) && draft >= 0 && draft <= total + 1e-9 ? Math.round((total - draft) * 100) / 100 : null;
  }

  async saveRate(row: AgentCommissionRow): Promise<void> {
    const percent = Number(this.rateDraft(row));
    if (this.remainder(row) === null) {
      this.toast.error(`Enter a rate from 0% to ${this.pct(row.allocation)}.`);
      return;
    }
    this.savingRate.set(row.agent_id);
    this.commissionApi.setAgentRate(row.agent_id, (percent / 100).toFixed(4)).subscribe({
      next: (saved) => {
        this.agentRows.update((rows) => rows?.map((r) => (r.agent_id === saved.agent_id ? saved : r)) ?? rows);
        this.rateDrafts.update(({ [row.agent_id]: _gone, ...rest }) => rest);
        this.savingRate.set(null);
        this.toast.success(`${saved.username}: ${this.pct(saved.agent_rate)} agent, ${this.pct(saved.master_rate)} yours. Applies to new bets.`);
      },
      error: (err: unknown) => { this.savingRate.set(null); this.toast.error(toApiError(err).message); },
    });
  }

  rateChanged(row: AgentCommissionRow): boolean {
    return Math.abs(Number(this.rateDraft(row)) - Number(row.agent_rate) * 100) > 1e-9;
  }

  filterAgent(agentId: string | null): void {
    this.historyAgent.set(agentId || null);
    this.historyPage.set(1);
    this.reloadCommission();
  }

  showBreakdown(by: 'player' | 'fight'): void {
    this.breakdownBy.set(by);
    this.reloadCommission();
  }

  goToPage(page: number): void {
    this.historyPage.set(Math.min(Math.max(1, page), this.historyPages()));
    this.reloadCommission();
  }

}

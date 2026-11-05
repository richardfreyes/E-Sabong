import { DatePipe, PercentPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Observable, Subject, catchError, forkJoin, map, of, switchMap } from 'rxjs';

import type { AdminDashboard, BalanceSummary, BettingConfig, PlayEntryKind, PlayWalletEntry, TenantProfile } from '@shared/models';
import { ArenaPointsPipe } from '@shared/pipes';
import { AdminApi, AuthStore, PortalApi, ToastService, toApiError } from '@shared/services';

import { AGENT_PORTAL } from './access';
import { agentShareLinks } from './links';

const ENTRY_LABEL: Record<PlayEntryKind, string> = {
  grant: 'Starting balance', stake: 'Bet placed', payout: 'Win credited', refund: 'Refund', resettlement: 'Result correction',
  account_grant: 'Account grant', uniform_grant: 'Uniform grant', reset: 'Reset', correction: 'Balance correction',
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
 * Every figure comes from an existing endpoint, scoped to the caller's tenant and downline by
 * the API. Nothing here moves balances. Commission earnings are not recorded by the platform,
 * so those cards show an unavailable state instead of numbers.
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

  readonly master = computed(() => this.auth.user()?.account_type === 'master_agent');
  readonly roleLabel = computed(() => (this.master() ? 'Master Agent' : 'Agent'));
  readonly displayName = computed(() => this.auth.user()?.full_name || this.auth.user()?.username || '');
  readonly handle = computed(() => this.auth.user()?.username ?? '');
  readonly links = computed(() => agentShareLinks(globalThis.location?.origin ?? '', this.handle()));
  readonly periodLabel = computed(() => (this.period() === 'current' ? 'Current Month' : 'Last Month'));
  readonly compareLabel = computed(() => (this.period() === 'current' ? 'Last Month' : 'Month Before'));
  /** Agents below a master agent. The API counts the caller too when it is itself an agent. */
  readonly agentCount = computed(() => {
    const byType = this.dash()?.users_by_type;
    if (!byType) return null;
    const agents = byType.agent ?? 0;
    return this.master() ? agents : Math.max(0, agents - 1);
  });
  readonly liveNow = computed(() => this.dash()?.matches_in_progress ?? null);
  readonly houseRate = computed(() => { const r = this.rates(); return r ? Number(r.commission_rate) : null; });
  readonly canSeeNetwork = computed(() => this.auth.hasPermission('points.view_downline'));

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

  entryLabel(kind: PlayEntryKind): string {
    return ENTRY_LABEL[kind] ?? kind;
  }
}

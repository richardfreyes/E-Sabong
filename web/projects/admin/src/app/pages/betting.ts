import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatTabsModule } from '@angular/material/tabs';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime } from 'rxjs';

import { CellDefDirective, Column, DataTableComponent, PageHeaderComponent, StatCardComponent } from '@shared/components';
import type { FightBookSummary, PlayEntryKind, PlayReconciliation, PlayStatement, PlayWalletEntry, PlayWalletRow, TenantProfile } from '@shared/models';
import { ArenaPointsPipe, formatArenaPoints, toCents } from '@shared/pipes';
import { AdminApi, AuthStore, PagedList, RealtimeService, toApiError } from '@shared/services';

type FightFilter = 'active' | 'settled' | 'all';
const OUTCOME: Record<string, string> = { meron: 'MERON', wala: 'WALA', draw: 'DRAW', no_contest: 'No contest', cancelled: 'Cancelled' };

/**
 * Simulated betting overview for administrators: every fight's live pools and odds across events,
 * settlements and refunds, play wallets and grants, the ledger and the reconciliation report. All
 * figures come from the API; realtime frames only trigger a refetch.
 */
@Component({
  selector: 'app-admin-betting',
  imports: [
    DatePipe, RouterLink, ArenaPointsPipe, MatTabsModule, MatButtonToggleModule, MatIconModule,
    PageHeaderComponent, StatCardComponent, DataTableComponent, CellDefDirective,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .date-filter { display: inline-flex; gap: 0.4rem; align-items: center; font-size: 0.8rem; color: var(--text-secondary); }
    .date-filter input { min-height: 2.25rem; padding: 0 0.4rem; border-radius: 0.5rem; border: 1px solid var(--mat-sys-outline-variant); background: transparent; color: inherit; font: inherit; }
    .statement { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: 0.5rem 1rem; margin: 0.75rem 0 0; }
    .statement dt { font-size: 0.75rem; color: var(--text-secondary); }
    .statement dd { margin: 0; font-variant-numeric: tabular-nums; }
    .fights { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 18.5rem), 1fr)); gap: 1rem; }
    .fight { display: flex; flex-direction: column; gap: 0.7rem; padding: 1rem; border: 1px solid var(--mat-sys-outline-variant); border-radius: 1rem; background: var(--surface); }
    .fight-head { display: flex; justify-content: space-between; gap: 0.5rem; align-items: baseline; }
    .fight-head a { color: inherit; font-weight: 700; text-decoration: none; }
    .fight-head a:hover { text-decoration: underline; }
    .event { margin: 0; font-size: 0.75rem; color: var(--mat-sys-on-surface-variant); }
    .state { font-size: 0.7rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--mat-sys-on-surface-variant); }
    .state.open { color: var(--success-text); } .state.closed { color: var(--warning-text); }
    .pools { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.5rem; margin: 0; }
    .pools div { padding-left: 0.5rem; border-left: 3px solid var(--side); min-width: 0; }
    .meron { --side: var(--meron); } .wala { --side: var(--wala); } .draw { --side: var(--draw); }
    dt { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--mat-sys-on-surface-variant); }
    dd { margin: 0; font-weight: 700; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
    dd small { display: block; font-weight: 500; color: var(--mat-sys-on-surface-variant); }
    .lines { display: grid; gap: 0.25rem; margin: 0; padding-top: 0.6rem; border-top: 1px solid var(--mat-sys-outline-variant); font-size: 0.8rem; }
    .lines div { display: flex; justify-content: space-between; gap: 0.5rem; }
    .lines dt { font-size: 0.8rem; text-transform: none; letter-spacing: 0; }
    .lines dd { font-weight: 600; }
    .toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0.75rem; margin: 1rem 0; }
    .muted { color: var(--mat-sys-on-surface-variant); font-size: 0.8rem; }
    .num { font-variant-numeric: tabular-nums; }
    .plus { color: var(--success-text); } .minus { color: var(--error-text); }
    .recon { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr)); gap: 1rem; margin-top: 1rem; }
    .recon section { padding: 1rem; border: 1px solid var(--mat-sys-outline-variant); border-radius: 1rem; }
    .recon h3 { margin: 0 0 0.6rem; font-size: 0.9rem; }
    .check { display: flex; align-items: center; gap: 0.4rem; font-size: 0.85rem; margin: 0.3rem 0; }
    .check mat-icon { width: 18px; height: 18px; font-size: 18px; }
    .ok { color: var(--success-text); } .bad { color: var(--error-text); }
  `,
  template: `
    <app-page-header title="Betting" subtitle="Simulated betting with player balances: live pools, settlements and where every allocation came from. Balances have no cash value." />
    <p class="m-0 mb-4 flex items-start gap-2 rounded-xl bg-secondary-container p-3 text-sm text-on-secondary-container" role="note">
      <mat-icon aria-hidden="true">info</mat-icon>Player balances have no cash value and cannot be bought, transferred, withdrawn or redeemed. Legacy points remain in a separate read-only archive.
    </p>

    @if (canSeeLedger()) {
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <app-stat-card label="Granted by the system" icon="redeem" [loading]="!recon()" [value]="money(recon()?.granted)" [hint]="recon() ? recon()!.grants + ' grants' : null" />
        <app-stat-card label="Held by players" icon="account_balance_wallet" [loading]="!recon()" [value]="money(recon()?.in_wallets)" [hint]="recon() ? recon()!.wallets + ' wallets' : null" />
        <app-stat-card label="At stake now" icon="hourglass_top" [loading]="!recon()" [value]="money(recon()?.at_stake)" />
        <app-stat-card label="House result" icon="account_balance" [loading]="!recon()" [value]="money(recon()?.house_result)" [hint]="recon() ? 'Deductions ' + money(recon()!.commission) : null" />
        <app-stat-card label="Ledger check" [icon]="recon()?.balanced ? 'verified' : 'error'" [tone]="recon() ? (recon()!.balanced ? 'success' : 'warning') : 'neutral'"
                       [loading]="!recon()" [value]="recon() ? (recon()!.balanced ? 'Balanced' : 'Check failed') : null" />
      </div>
    }

    <mat-tab-group class="mt-4" mat-stretch-tabs="false" animationDuration="0ms" [(selectedIndex)]="tab">
      <mat-tab label="Fights">
        <div class="toolbar">
          <mat-button-toggle-group [value]="fightFilter()" (change)="setFightFilter($event.value)" aria-label="Fights to show" hideSingleSelectionIndicator>
            <mat-button-toggle value="active">Open &amp; closed</mat-button-toggle>
            <mat-button-toggle value="settled">Settled</mat-button-toggle>
            <mat-button-toggle value="all">All</mat-button-toggle>
          </mat-button-toggle-group>
          <span class="muted" aria-live="polite">{{ fights.total() }} {{ fights.total() === 1 ? 'fight' : 'fights' }} · updates live</span>
        </div>
        @if (fights.error(); as e) { <p class="muted bad" role="alert">{{ e }}</p> }
        @if (!fights.rows().length && !fights.loading()) { <p class="muted">No fights with betting {{ fightFilter() === 'settled' ? 'settled yet' : 'open right now' }}.</p> }
        <div class="fights" [attr.aria-busy]="fights.loading()">
          @for (f of fights.rows(); track f.match_id) {
            <article class="fight" [attr.aria-label]="'Fight ' + f.match_number + ', ' + f.event_name">
              <div class="fight-head">
                <a [routerLink]="['/events', f.event_id]">Fight #{{ f.match_number }}</a>
                <span class="state" [class]="'state ' + (f.state ?? '')">{{ stateLabel(f) }}</span>
              </div>
              <p class="event">{{ f.event_name }} · {{ f.bet_count }} {{ f.bet_count === 1 ? 'bet' : 'bets' }} · {{ f.stakes_total | arenaPoints }} staked</p>
              <dl class="pools">
                <div class="meron"><dt>Meron</dt><dd>{{ f.pools.meron | arenaPoints }}<small>Odds {{ f.odds.meron_display ?? '—' }}</small></dd></div>
                <div class="wala"><dt>Wala</dt><dd>{{ f.pools.wala | arenaPoints }}<small>Odds {{ f.odds.wala_display ?? '—' }}</small></dd></div>
                <div class="draw"><dt>Draw</dt><dd>{{ f.pools.draw | arenaPoints }}<small>× {{ multiplier(f.draw_multiplier) }}</small></dd></div>
              </dl>
              @if (f.settlement; as s) {
                <dl class="lines">
                  <div><dt>Result</dt><dd>{{ outcome(s.outcome) }}{{ (s.revision ?? 1) > 1 ? ' (corrected)' : '' }}</dd></div>
                  <div><dt>Credited to winners</dt><dd class="num">{{ s.paid_to_winners | arenaPoints }}</dd></div>
                  <div><dt>Refunded</dt><dd class="num">{{ s.refunds | arenaPoints }}</dd></div>
                  <div><dt>Deduction</dt><dd class="num">{{ s.commission | arenaPoints }}</dd></div>
                  @if (hasValue(s.uncollected)) { <div><dt>Uncollected after correction</dt><dd class="num bad">{{ s.uncollected | arenaPoints }}</dd></div> }
                </dl>
              } @else if (f.obligations; as o) {
                <dl class="lines" aria-label="Credits returned by result">
                  <div><dt>If MERON wins, returns</dt><dd class="num">{{ o.meron | arenaPoints }}</dd></div>
                  <div><dt>If WALA wins, returns</dt><dd class="num">{{ o.wala | arenaPoints }}</dd></div>
                  <div><dt>If DRAW, returns</dt><dd class="num">{{ o.draw | arenaPoints }}</dd></div>
                  <div><dt>If cancelled, refunds</dt><dd class="num">{{ o.cancelled | arenaPoints }}</dd></div>
                </dl>
              }
            </article>
          }
        </div>
        @if (fights.total() > fights.rows().length) {
          <p class="muted">Showing {{ fights.rows().length }} of {{ fights.total() }}. <button type="button" class="text-primary" (click)="more()">Show more</button></p>
        }
      </mat-tab>

      @if (canSeeLedger()) {
        <mat-tab label="Wallets">
          <app-data-table class="mt-4 block" [columns]="walletColumns" [rows]="wallets.rows()" [total]="wallets.total()" [page]="wallets.page()" [pageSize]="wallets.pageSize()"
                          [loading]="wallets.loading()" [error]="wallets.error()" searchPlaceholder="Search player" emptyTitle="No play wallets yet" emptyIcon="account_balance_wallet"
                          [rowClickable]="true" (rowClick)="ledgerFor($event)" (search)="wallets.setSearch($event)" (pageChange)="wallets.setPage($event)" (retry)="wallets.load()">
            <ng-template appCell="balance" let-row><b class="num">{{ row.balance | arenaPoints }}</b></ng-template>
            <ng-template appCell="granted" let-row><span class="num">{{ row.granted | arenaPoints }}</span></ng-template>
            <ng-template appCell="staked" let-row><span class="num">{{ row.staked | arenaPoints }}</span></ng-template>
            <ng-template appCell="returned" let-row><span class="num">{{ row.returned | arenaPoints }}</span></ng-template>
            <ng-template appCell="open_stakes" let-row><span class="num">{{ row.open_stakes | arenaPoints }}</span></ng-template>
            <ng-template appCell="updated_at" let-row>{{ row.updated_at | date: 'short' }}</ng-template>
          </app-data-table>
        </mat-tab>

        <mat-tab label="Ledger">
          <app-data-table class="mt-4 block" [columns]="entryColumns" [rows]="entries.rows()" [total]="entries.total()" [page]="entries.page()" [pageSize]="entries.pageSize()"
                          [loading]="entries.loading()" [error]="entries.error()" [filters]="true" searchPlaceholder="Search player, reference or event"
                          emptyTitle="No ledger entries" emptyIcon="receipt_long" (search)="entries.setSearch($event)" (pageChange)="entries.setPage($event)" (retry)="entries.load()">
            <mat-button-toggle-group filters [value]="entryKind()" (change)="setEntryKind($event.value)" aria-label="Filter by entry type" hideSingleSelectionIndicator>
              <mat-button-toggle value="">All</mat-button-toggle>
              @for (k of kinds; track k) { <mat-button-toggle [value]="k">{{ kindLabel[k] }}</mat-button-toggle> }
            </mat-button-toggle-group>
            <label filters class="date-filter">From <input type="date" aria-label="Ledger from date" (change)="setEntryDate('date_from', $any($event.target).value)" /></label>
            <label filters class="date-filter">To <input type="date" aria-label="Ledger to date" (change)="setEntryDate('date_to', $any($event.target).value)" /></label>
            <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'medium' }}</ng-template>
            <ng-template appCell="kind" let-row>{{ kindText(row.kind) }}</ng-template>
            <ng-template appCell="delta" let-row><span class="num" [class.plus]="!row.delta.startsWith('-')" [class.minus]="row.delta.startsWith('-')">{{ signed(row.delta) }}</span></ng-template>
            <ng-template appCell="balance_after" let-row><span class="num">{{ row.balance_after | arenaPoints }}</span></ng-template>
            <ng-template appCell="fight" let-row>{{ row.match_number ? row.event_name + ' · #' + row.match_number : '—' }}</ng-template>
            <ng-template appCell="outcome" let-row>{{ row.bet_status ? (row.bet_side?.toUpperCase() + ' · ' + (betOutcome[row.bet_status] ?? row.bet_status)) : '—' }}</ng-template>
          </app-data-table>
          @if (entryUser(); as u) {
            <p class="muted">Showing {{ u }}'s entries. <button type="button" class="text-primary" (click)="clearUser()">Show everyone</button></p>
            @if (statement(); as t) {
              <dl class="statement" aria-label="Player statement" data-testid="player-statement">
                <div><dt>Balance</dt><dd>{{ t.balance | arenaPoints }}</dd></div>
                <div><dt>Staked</dt><dd>{{ t.staked | arenaPoints }} · {{ t.bets }} bets</dd></div>
                <div><dt>Wins credited</dt><dd>{{ t.winnings_credited | arenaPoints }} · {{ t.bets_won }} won</dd></div>
                <div><dt>Lost stakes</dt><dd>{{ t.lost_stakes | arenaPoints }} · {{ t.bets_lost }} lost</dd></div>
                <div><dt>Refunds</dt><dd>{{ t.refunds | arenaPoints }} · {{ t.bets_refunded }} refunded</dd></div>
                <div><dt>Corrections</dt><dd>{{ signed(t.corrections) }}</dd></div>
                <div><dt>Net result</dt><dd>{{ signed(t.net_result) }}</dd></div>
                <div><dt>Open bets</dt><dd>{{ t.bets_open }} · {{ t.open_stakes | arenaPoints }}</dd></div>
              </dl>
            }
          }
        </mat-tab>

        <mat-tab label="Reconciliation">
          @if (recon(); as r) {
            <div class="recon">
              <section aria-labelledby="flow-title">
                <h3 id="flow-title">Where every play point is</h3>
                <dl class="lines">
                  <div><dt>Granted by the system</dt><dd class="num">{{ r.granted | arenaPoints }}</dd></div>
                  <div><dt>Added to specific accounts</dt><dd class="num">{{ (r.account_granted ?? '0.00') | arenaPoints }}</dd></div>
                  <div><dt>Uniform grants</dt><dd class="num">{{ (r.uniform_granted ?? '0.00') | arenaPoints }}</dd></div>
                  <div><dt>Resets and balance corrections (net)</dt><dd class="num">{{ adminNet(r) }}</dd></div>
                  <div><dt>Held by players</dt><dd class="num">{{ r.in_wallets | arenaPoints }}</dd></div>
                  <div><dt>At stake (unsettled bets)</dt><dd class="num">{{ r.at_stake | arenaPoints }}</dd></div>
                  <div><dt>House result</dt><dd class="num">{{ r.house_result | arenaPoints }}</dd></div>
                </dl>
                <p class="muted">Starting grants + account grants + uniform grants + resets and corrections = in wallets + at stake + house result.</p>
              </section>
              <section aria-labelledby="moves-title">
                <h3 id="moves-title">Ledger movements</h3>
                <dl class="lines">
                  <div><dt>Staked</dt><dd class="num">{{ r.staked | arenaPoints }}</dd></div>
                  <div><dt>Credited to winners</dt><dd class="num">{{ r.paid_to_winners | arenaPoints }}</dd></div>
                  <div><dt>Refunded</dt><dd class="num">{{ r.refunded | arenaPoints }}</dd></div>
                  <div><dt>Resettled after corrections</dt><dd class="num">{{ r.resettled | arenaPoints }}</dd></div>
                  <div><dt>Deductions retained</dt><dd class="num">{{ r.commission | arenaPoints }}</dd></div>
                </dl>
              </section>
              <section aria-labelledby="checks-title">
                <h3 id="checks-title">Integrity checks</h3>
                @for (c of checks(); track c.label) {
                  <p class="check" [class.ok]="c.ok" [class.bad]="!c.ok"><mat-icon aria-hidden="true">{{ c.ok ? 'check_circle' : 'error' }}</mat-icon>{{ c.label }}</p>
                }
              </section>
            </div>
          } @else if (reconError(); as e) { <p class="muted bad" role="alert">{{ e }}</p> }
        </mat-tab>
      }
    </mat-tab-group>
  `,
})
export class BettingPage {
  private readonly api = inject(AdminApi);
  private readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly canSeeLedger = computed(() => this.auth.hasPermission('points.view_all'));

  readonly tab = signal(0);
  readonly fightFilter = signal<FightFilter>('active');
  readonly entryKind = signal<PlayEntryKind | ''>('');
  readonly entryUser = signal<string | null>(null);
  readonly recon = signal<PlayReconciliation | null>(null);
  readonly reconError = signal<string | null>(null);
  readonly kinds: PlayEntryKind[] = ['grant', 'stake', 'payout', 'refund', 'resettlement', 'account_grant', 'uniform_grant', 'reset', 'correction', 'transfer'];
  /** Ledger kinds keep their stored values; only their wording avoids cash terms. */
  readonly kindLabel: Record<PlayEntryKind, string> = {
    grant: 'System grant', stake: 'Bet', payout: 'Win credited', refund: 'Refund', resettlement: 'Correction',
    account_grant: 'Account grant', uniform_grant: 'Uniform grant', reset: 'Reset', correction: 'Balance correction',
    transfer: 'Balance transfer', earnings: 'Earnings moved to play balance',
  };
  readonly kindText = (kind: string): string => this.kindLabel[kind as PlayEntryKind] ?? kind;

  readonly fights = new PagedList<FightBookSummary>((q) => this.api.bettingBooks(q), { state: ['open', 'closed'] });
  readonly wallets = new PagedList<PlayWalletRow>((q) => this.api.playWallets(q));
  readonly entries = new PagedList<PlayWalletEntry>((q) => this.api.playEntries(q));

  readonly walletColumns: Column<PlayWalletRow>[] = [
    { key: 'username', label: 'Player' },
    { key: 'balance', label: 'Balance', align: 'end' },
    { key: 'granted', label: 'Granted', align: 'end' },
    { key: 'staked', label: 'Staked', align: 'end' },
    { key: 'returned', label: 'Returned', align: 'end' },
    { key: 'open_stakes', label: 'At stake', align: 'end' },
    { key: 'updated_at', label: 'Last activity' },
  ];
  readonly entryColumns: Column<PlayWalletEntry>[] = [
    { key: 'created_at', label: 'When' },
    { key: 'username', label: 'Player' },
    { key: 'kind', label: 'Type' },
    { key: 'delta', label: 'Credits', align: 'end' },
    { key: 'balance_after', label: 'Credits after', align: 'end' },
    { key: 'fight', label: 'Fight' },
    { key: 'outcome', label: 'Bet outcome', hideOnMobile: true },
    { key: 'reference', label: 'Reference', hideOnMobile: true },
  ];
  /** A bet's outcome on its ledger rows (a lost bet has no ledger row of its own). */
  readonly betOutcome: Record<string, string> = { accepted: 'Open', won: 'Won', lost: 'Lost', refunded: 'Refunded' };
  readonly statement = signal<PlayStatement | null>(null);
  private entryUserId: string | null = null;
  private readonly entryRange: Record<'date_from' | 'date_to', string | null> = { date_from: null, date_to: null };

  readonly checks = computed(() => {
    const r = this.recon();
    if (!r) return [];
    return [
      { ok: r.checks.wallets_match_ledger, label: 'Wallet total equals the ledger total' },
      { ok: r.checks.wallet_mismatches === 0, label: `${r.checks.wallet_mismatches} wallets differ from their own entries` },
      { ok: r.checks.pool_mismatches === 0, label: `${r.checks.pool_mismatches} fight pools differ from their bets` },
      { ok: r.checks.uncollected_bets === 0, label: `${r.checks.uncollected_bets} corrected wins not fully reclaimed (${this.money(r.checks.uncollected_amount)})` },
    ];
  });

  constructor() {
    this.fights.pageSize.set(24);
    this.fights.load();
    if (this.canSeeLedger()) {
      this.wallets.load();
      this.entries.load();
      this.loadRecon();
    }
    // Frames carry no private data here; they only say something changed. Bursts collapse.
    const refresh = new Subject<void>();
    const destroy = inject(DestroyRef);
    refresh.pipe(debounceTime(400), takeUntilDestroyed(destroy)).subscribe(() => this.reloadAll());
    inject(RealtimeService)
      .on('book.updated', 'betting.closed', 'fight.result_declared', 'fight.settled', 'betting.ledger_updated', 'connected')
      .pipe(takeUntilDestroyed(destroy))
      .subscribe(() => refresh.next());
  }

  readonly money = (v: string | null | undefined) => (v === null || v === undefined ? null : formatArenaPoints(toCents(v) ?? 0));
  readonly outcome = (o: string) => OUTCOME[o] ?? o;
  readonly multiplier = (m: string) => String(Number(m));
  readonly hasValue = (v: string) => (toCents(v) ?? 0) !== 0;

  /** Net of administrator resets and balance corrections (either sign). */
  adminNet(r: PlayReconciliation): string {
    const cents = (toCents(r.reset_net ?? '0') ?? 0) + (toCents(r.corrections_net ?? '0') ?? 0);
    return `${cents < 0 ? '−' : ''}${formatArenaPoints(Math.abs(cents))}`;
  }

  signed(delta: string): string {
    const cents = toCents(delta) ?? 0;
    return `${cents > 0 ? '+' : cents < 0 ? '−' : ''}${formatArenaPoints(Math.abs(cents))}`;
  }

  stateLabel(f: FightBookSummary): string {
    if (f.state === 'settled') return `Settled · ${this.outcome(f.outcome ?? '')}`;
    if (f.state === 'open') return f.accepting ? 'Betting open' : 'Open · not accepting';
    return 'Betting closed';
  }

  setFightFilter(filter: FightFilter): void {
    this.fightFilter.set(filter);
    this.fights.setFilter('state', filter === 'active' ? ['open', 'closed'] : filter === 'settled' ? ['settled'] : null);
  }

  more(): void {
    this.fights.setPage({ page: 1, pageSize: Math.min(96, this.fights.pageSize() + 24) });
  }

  setEntryKind(kind: PlayEntryKind | ''): void {
    this.entryKind.set(kind);
    this.entries.setFilter('kind', kind || null);
  }

  ledgerFor(row: PlayWalletRow): void {
    this.entryUser.set(row.username);
    this.entryUserId = row.user_id;
    this.entries.setFilter('user_id', row.user_id);
    this.loadStatement();
    this.tab.set(2);
  }

  clearUser(): void {
    this.entryUser.set(null);
    this.entryUserId = null;
    this.statement.set(null);
    this.entries.setFilter('user_id', null);
  }

  /** UTC calendar-day range for the ledger and, when one player is selected, their statement totals. */
  setEntryDate(key: 'date_from' | 'date_to', value: string): void {
    this.entryRange[key] = value || null;
    this.entries.setFilter(key, value || null);
    this.loadStatement();
  }

  private loadStatement(): void {
    if (!this.entryUserId) return;
    this.api.playStatement(this.entryUserId, { ...this.entryRange }).subscribe({
      next: (s) => this.statement.set(s),
      error: () => this.statement.set(null),
    });
  }

  private reloadAll(): void {
    this.fights.load();
    if (this.canSeeLedger()) {
      this.wallets.load();
      this.entries.load();
      this.loadRecon();
    }
  }

  private loadRecon(): void {
    this.api.playReconciliation().subscribe({
      next: (r) => (this.recon.set(r), this.reconError.set(null)),
      error: (e) => this.reconError.set(toApiError(e).message),
    });
  }
}

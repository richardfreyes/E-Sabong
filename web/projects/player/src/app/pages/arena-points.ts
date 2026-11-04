import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, effect, inject, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatPaginatorModule } from '@angular/material/paginator';

import type { PlayEntryKind, PlayStatement, PlayWalletEntry } from '@shared/models';
import { ArenaPointsPipe, toCents } from '@shared/pipes';
import { PagedList, PortalApi, RealtimeService } from '@shared/services';
import { PlayWalletStore } from '../core/play-betting';
import { PageHeadComponent } from '../ui/page-head';

const KIND_LABEL: Record<PlayEntryKind, string> = {
  grant: 'Starting balance', stake: 'Bet placed', payout: 'Win credited', refund: 'Refund', resettlement: 'Correction',
  account_grant: 'Balance added', uniform_grant: 'Uniform grant', reset: 'Balance reset', correction: 'Balance correction',
};
const KINDS: PlayEntryKind[] = ['grant', 'stake', 'payout', 'refund', 'resettlement', 'account_grant', 'uniform_grant', 'reset', 'correction'];
/** A bet's outcome as the statement shows it (a lost bet has no ledger row of its own). */
const OUTCOME: Record<string, string> = { accepted: 'Open', won: 'Won', lost: 'Lost', refunded: 'Refunded' };

/**
 * The player's balance: the same balance as the header and the betting panel (PlayWalletStore)
 * and their own ledger from the API, newest first. Read-only; nothing here changes a balance.
 */
@Component({
  selector: 'app-player-arena-points',
  imports: [DatePipe, MatButtonModule, MatIconModule, MatPaginatorModule, ArenaPointsPipe, PageHeadComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .hero { display: grid; grid-template-columns: auto 1fr; gap: 1.5rem; align-items: center; padding: 1.4rem 1.5rem; margin-bottom: 1rem;
      border-radius: 1.25rem; border: 1px solid color-mix(in srgb, var(--mat-sys-primary) 35%, var(--pp-rope));
      background: radial-gradient(ellipse 60% 120% at 0% 0%, color-mix(in srgb, var(--mat-sys-primary) 16%, transparent), transparent 70%), var(--pp-pit); }
    .label { margin: 0; color: var(--pp-ash); font-size: 0.85rem; }
    .hero > div { min-width: 0; }
    .balance { overflow-wrap: anywhere; margin: 0.4rem 0 0; font-size: clamp(3rem, 2.4rem + 3vw, 4.75rem); color: var(--pp-chalk); }
    .note { display: flex; gap: 0.6rem; margin: 0; color: var(--pp-ash); font-size: 0.875rem; max-width: 46ch; justify-self: end; }
    .note mat-icon { flex: none; color: var(--mat-sys-primary); }
    ul { list-style: none; margin: 0; padding: 0; }
    li { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, auto); gap: 0.25rem 1rem; padding: 0.75rem 1.1rem; }
    li + li { border-top: 1px solid var(--pp-rope); }
    .what { color: var(--pp-chalk); font-weight: 500; }
    .meta { color: var(--pp-ash); font-size: 0.8rem; }
    .delta { overflow-wrap: anywhere; font-size: 1.4rem; text-align: right; color: var(--pp-chalk); }
    .delta.out { color: var(--pp-ash); }
    .after { overflow-wrap: anywhere; text-align: right; color: var(--pp-ash); font-size: 0.8rem; font-variant-numeric: tabular-nums; }
    .empty { padding: 1.25rem 1.1rem; color: var(--pp-ash); }
    .delta.in { color: var(--mat-sys-primary); }
    .error { padding: 1.25rem 1.1rem; color: var(--mat-sys-error); display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }
    .totals { display: grid; grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr)); gap: 0.6rem; margin-bottom: 1rem; }
    .tile { padding: 0.8rem 1rem; border-radius: 0.9rem; border: 1px solid var(--pp-rope); background: var(--pp-pit); min-width: 0; }
    .tile span { display: block; color: var(--pp-ash); font-size: 0.75rem; }
    .tile strong { display: block; overflow-wrap: anywhere; color: var(--pp-chalk); font-size: 1.15rem; margin-top: 0.2rem; }
    .tile small { color: var(--pp-ash); font-size: 0.72rem; }
    .filters { display: flex; flex-wrap: wrap; gap: 0.6rem 1rem; align-items: end; padding: 0.75rem 1.1rem; border-bottom: 1px solid var(--pp-rope); }
    .filters label { display: grid; gap: 0.2rem; color: var(--pp-ash); font-size: 0.75rem; }
    .filters select, .filters input { min-height: 2.25rem; padding: 0 0.5rem; border-radius: 0.5rem; border: 1px solid var(--pp-rope);
      background: var(--pp-pit-raised); color: var(--pp-chalk); font: inherit; font-size: 0.85rem; }
    .outcome { margin-left: 0.4rem; padding: 0.05rem 0.45rem; border-radius: 999px; font-size: 0.7rem; border: 1px solid var(--pp-rope); color: var(--pp-ash); }
    .outcome.won { color: var(--mat-sys-primary); border-color: currentColor; }
    .skeleton { height: 3.5rem; margin: 0.75rem 1.1rem; border-radius: 0.6rem; background: var(--pp-pit-raised); animation: pp-pulse 1.6s ease-in-out infinite; }
    @media (max-width: 767px) { .hero { grid-template-columns: 1fr; gap: 0.75rem; } .note { justify-self: start; } }
  `,
  template: `
    <pp-page-head title="Balance history" />
    <section class="hero" aria-label="Balance">
      <div>
        <p class="label">Available Balance</p>
        <p class="balance pp-num" data-field="balance">{{ wallet.balance() | arenaPoints }}</p>
      </div>
      <p class="note"><mat-icon aria-hidden="true">info</mat-icon>Your balance is for simulated play only. It has no cash value and
        cannot be bought, transferred, withdrawn or redeemed.</p>
    </section>
    @if (summary(); as t) {
      <section class="totals" aria-label="Statement totals" data-testid="statement-totals">
        <div class="tile"><span>Staked</span><strong class="pp-num">{{ t.staked | arenaPoints }}</strong><small>{{ t.bets }} bets</small></div>
        <div class="tile"><span>Wins credited</span><strong class="pp-num">{{ t.winnings_credited | arenaPoints }}</strong><small>{{ t.bets_won }} won</small></div>
        <div class="tile"><span>Lost stakes</span><strong class="pp-num">{{ t.lost_stakes | arenaPoints }}</strong><small>{{ t.bets_lost }} lost</small></div>
        <div class="tile"><span>Refunds</span><strong class="pp-num">{{ t.refunds | arenaPoints }}</strong><small>{{ t.bets_refunded }} refunded</small></div>
        <div class="tile"><span>Corrections</span><strong class="pp-num">{{ signed(t.corrections) }}</strong><small>Result changes</small></div>
        @if (hasAdjustments(t)) {
          <div class="tile" data-testid="admin-adjustments"><span>Grants and resets</span><strong class="pp-num">{{ adjustments(t) }}</strong><small>By an administrator</small></div>
        }
        <div class="tile"><span>Net result</span><strong class="pp-num" data-field="net">{{ signed(t.net_result) }}</strong><small>{{ t.bets_open }} open · {{ t.open_stakes | arenaPoints }}</small></div>
      </section>
    }
    <section class="pp-panel overflow-hidden" aria-labelledby="statement">
      <div class="pp-panel-head"><h2 id="statement" class="pp-h2">Statement</h2></div>
      <div class="filters">
        <label>Type<select aria-label="Entry type" [value]="kind()" (change)="setFilter('kind', $any($event.target).value)">
          <option value="">All activity</option>
          @for (k of kinds; track k) { <option [value]="k">{{ kindLabel[k] }}</option> }
        </select></label>
        <label>From<input type="date" aria-label="From date" [value]="from()" (change)="setFilter('date_from', $any($event.target).value)" /></label>
        <label>To<input type="date" aria-label="To date" [value]="to()" (change)="setFilter('date_to', $any($event.target).value)" /></label>
      </div>
      @if (list.error(); as e) {
        <p class="error" role="alert">{{ e }} <button mat-stroked-button type="button" (click)="list.load()">Try again</button></p>
      } @else if (list.loading() && !list.rows().length) {
        @for (i of [1, 2, 3]; track i) { <div class="skeleton"></div> }
      } @else {
        <ul>
          @for (e of list.rows(); track e.id) {
            <li>
              <span class="what">{{ kindLabel[e.kind] ?? e.kind }}@if (e.operation_reference) { · {{ e.operation_reference }}}@if (e.bet_side) { · {{ e.bet_side.toUpperCase() }}}@if (e.match_number) { · Fight #{{ e.match_number }}@if (e.event_name) {, {{ e.event_name }}}}@if (e.kind === 'stake' && e.bet_status) {<span class="outcome" [class]="'outcome ' + e.bet_status" data-field="outcome">{{ outcome[e.bet_status] ?? e.bet_status }}</span>}</span>
              <span class="pp-num delta" [class.in]="isCredit(e)" [class.out]="!isCredit(e)">{{ signed(e.delta) }}</span>
              <span class="meta">{{ e.created_at | date: 'MMM d, y, h:mm a' : '+0800' }}</span>
              <span class="after">Balance {{ e.balance_after | arenaPoints }}</span>
            </li>
          } @empty {
            <li class="empty">No balance activity yet. Your starting balance, bets and wins appear here.</li>
          }
        </ul>
      }
    </section>
    @if (list.total() > list.pageSize()) {
      <mat-paginator class="mt-3" [length]="list.total()" [pageIndex]="list.page() - 1" [pageSize]="list.pageSize()"
                     (page)="list.setPage({ page: $event.pageIndex + 1, pageSize: $event.pageSize })" aria-label="Select page" />
    }
  `,
})
export class ArenaPointsPage {
  private readonly api = inject(PortalApi);
  readonly wallet = inject(PlayWalletStore);
  readonly kindLabel = KIND_LABEL;
  readonly kinds = KINDS;
  readonly outcome = OUTCOME;
  readonly list = new PagedList<PlayWalletEntry>((q) => this.api.arenaPointsHistory(q));
  readonly summary = signal<PlayStatement | null>(null);
  readonly kind = signal('');
  readonly from = signal('');
  readonly to = signal('');

  constructor() {
    this.wallet.load();
    effect(() => { this.wallet.balance(); untracked(() => this.reload()); });
    // A balance change anywhere (bet, settlement, refund) adds a ledger row: reload the statement.
    inject(RealtimeService).on('wallet.updated', 'connected').pipe(takeUntilDestroyed(inject(DestroyRef))).subscribe(() => this.reload());
  }

  /** Type and date filters (UTC days, as the server applies them). Totals follow the dates, not the type. */
  setFilter(key: 'kind' | 'date_from' | 'date_to', value: string): void {
    ({ kind: this.kind, date_from: this.from, date_to: this.to })[key].set(value);
    this.list.setFilter(key, value || null);
    if (key !== 'kind') this.loadSummary();
  }

  private reload(): void {
    this.list.load();
    this.loadSummary();
  }

  private loadSummary(): void {
    const range = { date_from: this.from() || null, date_to: this.to() || null };
    this.api.myStatement(range).subscribe({ next: (s) => this.summary.set(s), error: () => this.summary.set(null) });
  }

  isCredit(e: PlayWalletEntry): boolean {
    return (toCents(e.delta) ?? 0) > 0;
  }

  /** "+500.00" / "−100.00" from the server's decimal delta. */
  /** Account grants, uniform grants, resets and reviewed corrections by an administrator (net). */
  private adjustmentCents(t: PlayStatement): number {
    return ['account_grants', 'uniform_grants', 'resets', 'balance_corrections'].reduce(
      (sum, k) => sum + (toCents((t as unknown as Record<string, string | undefined>)[k] ?? '0') ?? 0), 0);
  }

  hasAdjustments(t: PlayStatement): boolean {
    return [t.account_grants, t.uniform_grants, t.resets, t.balance_corrections].some((v) => (toCents(v ?? '0') ?? 0) !== 0);
  }

  adjustments(t: PlayStatement): string {
    return this.signed((this.adjustmentCents(t) / 100).toFixed(2));
  }

  signed(delta: string): string {
    const cents = toCents(delta);
    if (cents === null) return '—';
    const text = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(cents) / 100);
    return cents > 0 ? `+${text}` : cents < 0 ? `−${text}` : text;
  }
}

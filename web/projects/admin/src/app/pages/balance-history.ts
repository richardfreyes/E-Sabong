import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';

import { CellDefDirective, Column, DataTableComponent, PageHeaderComponent } from '@shared/components';
import type { PlayEntryKind, PlayStatement, PlayWalletEntry, User } from '@shared/models';
import { ArenaPointsPipe, formatArenaPoints, toCents } from '@shared/pipes';
import { AdminApi, PagedList, toApiError } from '@shared/services';
import { UserPickerComponent } from './user-picker';

const KIND_LABEL: Record<PlayEntryKind, string> = {
  grant: 'Starting balance', stake: 'Bet placed', payout: 'Win credited', refund: 'Refund', resettlement: 'Correction',
  account_grant: 'Deposit', uniform_grant: 'Uniform grant', reset: 'Reset', correction: 'Balance correction',
  transfer: 'Transfer Balance', earnings: 'Withdrawal Commission',
};
/** A bet's outcome on its ledger rows (a lost bet adds no ledger row of its own; its stake row reads "Lost"). */
const BET_OUTCOME: Record<string, string> = { accepted: 'Open', won: 'Won', lost: 'Lost', refunded: 'Refunded' };

/**
 * Read-only balance history for staff who may view the play-balance ledger (`points.view_all`, enforced by the
 * API). Reuses `GET /admin/betting/entries` and `GET /admin/betting/statements/{user_id}`; nothing here changes
 * a balance. Balances are simulated and have no cash value.
 */
@Component({
  selector: 'app-balance-history',
  imports: [DatePipe, ArenaPointsPipe, MatButtonModule, MatButtonToggleModule, MatIconModule, PageHeaderComponent, DataTableComponent,
    CellDefDirective, UserPickerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .controls { display: grid; grid-template-columns: minmax(16rem, 26rem) auto auto; gap: 0.75rem 1rem; align-items: center; margin-bottom: 1rem; }
    .date-filter { display: inline-flex; gap: 0.4rem; align-items: center; font-size: 0.8rem; color: var(--text-secondary); }
    .date-filter input { min-height: 2.5rem; padding: 0 0.5rem; border-radius: 0.5rem; border: 1px solid var(--mat-sys-outline-variant);
      background: transparent; color: inherit; font: inherit; }
    .totals { display: grid; grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr)); gap: 0.6rem; margin: 0 0 1rem; }
    .tile { padding: 0.75rem 1rem; border-radius: 0.9rem; border: 1px solid var(--mat-sys-outline-variant); background: var(--surface); min-width: 0; }
    .tile dt { font-size: 0.75rem; color: var(--text-secondary); }
    .tile dd { margin: 0.2rem 0 0; font-size: 1.1rem; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
    .tile small { display: block; font-size: 0.72rem; color: var(--text-secondary); }
    .state { margin: 0 0 1rem; font-size: 0.85rem; color: var(--text-secondary); }
    .state.error { color: var(--error-text); display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }
    .num { font-variant-numeric: tabular-nums; } .plus { color: var(--success-text); } .minus { color: var(--text-secondary); }
    .ref { font-family: ui-monospace, monospace; font-size: 0.75rem; overflow-wrap: anywhere; }
    @media (max-width: 900px) { .controls { grid-template-columns: 1fr; } }
  `,
  template: `
    <app-page-header title="Balance History" subtitle="Read-only statement of player balances: bets, wins credited, losses, refunds and corrections." />
    <p class="m-0 mb-4 flex items-start gap-2 rounded-xl bg-secondary-container p-3 text-sm text-on-secondary-container" role="note" data-testid="no-cash-value">
      <mat-icon aria-hidden="true">info</mat-icon>Balances are for simulated play only. They have no cash value and cannot be bought, transferred, withdrawn or redeemed.
    </p>

    <div class="controls">
      <app-user-picker label="Player" accountType="player" hint="Leave empty to see every player's entries." (selection)="selectPlayer($event)" />
      <label class="date-filter">From (UTC) <input type="date" aria-label="From date (UTC)" [value]="from()" (change)="setDate('date_from', $any($event.target).value)" /></label>
      <label class="date-filter">To (UTC) <input type="date" aria-label="To date (UTC)" [value]="to()" (change)="setDate('date_to', $any($event.target).value)" /></label>
    </div>

    @if (player(); as p) {
      @if (statementError(); as e) {
        <p class="state error" role="alert">{{ e }} <button mat-stroked-button type="button" (click)="loadStatement()">Try again</button></p>
      } @else if (statement(); as t) {
        <dl class="totals" aria-label="Statement totals for {{ p.username }}" data-testid="statement-totals">
          <div class="tile"><dt>Balance (now)</dt><dd>{{ t.balance | arenaPoints }}</dd><small>Starting balance {{ t.granted | arenaPoints }}</small></div>
          <div class="tile"><dt>Staked</dt><dd>{{ t.staked | arenaPoints }}</dd><small>{{ t.bets }} bets</small></div>
          <div class="tile"><dt>Wins credited</dt><dd>{{ t.winnings_credited | arenaPoints }}</dd><small>{{ t.bets_won }} won</small></div>
          <div class="tile"><dt>Losses</dt><dd>{{ t.lost_stakes | arenaPoints }}</dd><small>{{ t.bets_lost }} lost</small></div>
          <div class="tile"><dt>Refunds</dt><dd>{{ t.refunds | arenaPoints }}</dd><small>{{ t.bets_refunded }} refunded</small></div>
          <div class="tile"><dt>Corrections</dt><dd>{{ signed(t.corrections) }}</dd><small>Result changes</small></div>
          <div class="tile"><dt>Net result</dt><dd data-field="net">{{ signed(t.net_result) }}</dd><small>{{ t.bets_open }} open · {{ t.open_stakes | arenaPoints }}</small></div>
        </dl>
      } @else {
        <p class="state" role="status">Loading {{ p.username }}'s statement…</p>
      }
    }

    <app-data-table [columns]="columns" [rows]="entries.rows()" [total]="entries.total()" [page]="entries.page()" [pageSize]="entries.pageSize()"
                    [loading]="entries.loading()" [error]="entries.error()" [filters]="true" searchPlaceholder="Search player, reference or event"
                    [emptyTitle]="player() ? 'No balance activity for this player' : 'No balance activity'" emptyIcon="receipt_long"
                    (search)="entries.setSearch($event)" (pageChange)="entries.setPage($event)" (retry)="entries.load()">
      <mat-button-toggle-group filters [value]="kind()" (change)="setKind($event.value)" aria-label="Filter by transaction type" hideSingleSelectionIndicator>
        <mat-button-toggle value="">All</mat-button-toggle>
        @for (k of kinds; track k) { <mat-button-toggle [value]="k">{{ kindLabel[k] }}</mat-button-toggle> }
      </mat-button-toggle-group>
      <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'MMM d, y, HH:mm' : 'UTC' }} UTC</ng-template>
      <ng-template appCell="kind" let-row>{{ kindText(row.kind) }}</ng-template>
      <ng-template appCell="fight" let-row>{{ row.match_number ? (row.event_name ?? 'Event') + ' · #' + row.match_number : '—' }}</ng-template>
      <ng-template appCell="outcome" let-row>{{ row.bet_status ? (row.bet_side?.toUpperCase() + ' · ' + (betOutcome[row.bet_status] ?? row.bet_status)) : '—' }}</ng-template>
      <ng-template appCell="delta" let-row><span class="num" [class.plus]="!row.delta.startsWith('-')" [class.minus]="row.delta.startsWith('-')">{{ signed(row.delta) }}</span></ng-template>
      <ng-template appCell="balance_after" let-row><span class="num">{{ row.balance_after | arenaPoints }}</span></ng-template>
      <ng-template appCell="reference" let-row><span class="ref">{{ row.reference }}</span></ng-template>
    </app-data-table>
  `,
})
export class BalanceHistoryPage {
  private readonly api = inject(AdminApi);
  readonly kindLabel = KIND_LABEL;
  readonly kinds = Object.keys(KIND_LABEL) as PlayEntryKind[];
  readonly betOutcome = BET_OUTCOME;
  readonly kindText = (kind: string): string => KIND_LABEL[kind as PlayEntryKind] ?? kind;
  readonly columns: Column<PlayWalletEntry>[] = [
    { key: 'created_at', label: 'When (UTC)' },
    { key: 'username', label: 'Player' },
    { key: 'kind', label: 'Type' },
    { key: 'fight', label: 'Fight', hideOnMobile: true },
    { key: 'outcome', label: 'Bet outcome' },
    { key: 'delta', label: 'Change', align: 'end' },
    { key: 'balance_after', label: 'Balance after', align: 'end' },
    { key: 'reference', label: 'Reference', hideOnMobile: true },
  ];

  readonly entries = new PagedList<PlayWalletEntry>((q) => this.api.playEntries(q));
  readonly player = signal<User | null>(null);
  readonly kind = signal<PlayEntryKind | ''>('');
  readonly from = signal('');
  readonly to = signal('');
  readonly statement = signal<PlayStatement | null>(null);
  readonly statementError = signal<string | null>(null);

  constructor() {
    this.entries.load();
  }

  selectPlayer(user: User | null): void {
    if ((user?.id ?? null) === (this.player()?.id ?? null)) return;
    this.player.set(user);
    this.entries.setFilter('user_id', user?.id ?? null);
    this.loadStatement();
  }

  setKind(kind: PlayEntryKind | ''): void {
    this.kind.set(kind);
    this.entries.setFilter('kind', kind || null);
  }

  /** UTC calendar days, applied by the server to the ledger and to the selected player's totals. */
  setDate(key: 'date_from' | 'date_to', value: string): void {
    (key === 'date_from' ? this.from : this.to).set(value);
    this.entries.setFilter(key, value || null);
    this.loadStatement();
  }

  loadStatement(): void {
    const user = this.player();
    this.statement.set(null);
    this.statementError.set(null);
    if (!user) return;
    this.api.playStatement(user.id, { date_from: this.from() || null, date_to: this.to() || null }).subscribe({
      next: (s) => { if (this.player()?.id === user.id) this.statement.set(s); },
      error: (e) => { if (this.player()?.id === user.id) this.statementError.set(toApiError(e).message); },
    });
  }

  signed(delta: string): string {
    const cents = toCents(delta) ?? 0;
    return `${cents > 0 ? '+' : cents < 0 ? '−' : ''}${formatArenaPoints(Math.abs(cents))}`;
  }
}

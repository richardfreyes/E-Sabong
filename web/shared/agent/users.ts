import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { Subject, catchError, debounceTime, forkJoin, map, of, switchMap } from 'rxjs';

import type { TenantProfile } from '@shared/models';
import { ArenaPointsPipe } from '@shared/pipes';
import {
  AgentUsersApi, AuthStore, BalanceTransfer, CommissionApi, MyUserKind, MyUserRow, MyUserState, MyUsersSummary, RealtimeService,
  ToastService, TransferPreview, newIdempotencyKey, toApiError,
} from '@shared/services';

interface Filter { kind: MyUserKind; state: MyUserState }

const STATES: { state: MyUserState; label: string; icon: string }[] = [
  { state: 'active', label: 'Active', icon: 'how_to_reg' },
  { state: 'online', label: 'Online', icon: 'wifi' },
  { state: 'blocked', label: 'Blocked', icon: 'person_off' },
  { state: 'banned', label: 'Banned', icon: 'block' },
];

/**
 * My Users: the players (and, for a master agent, the agents) below the caller, after the SWC V2
 * agent reference. Status columns: Allowed / Blocked (the upline may block or unblock), Banned
 * (administrators only), Online now. A master agent sets each direct agent's commission rate.
 * Add Balance moves wallet points to a player. A master agent can also move them to an agent.
 * Earnings are never used.
 */
@Component({
  selector: 'app-agent-users',
  imports: [MatIconModule, MatMenuModule, ArenaPointsPipe, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './users.scss',
  templateUrl: './users.html',
})
export class AgentUsersPage {
  private readonly api = inject(AgentUsersApi);
  private readonly destroyRef = inject(DestroyRef);
  private readonly commissions = inject(CommissionApi);
  private readonly toast = inject(ToastService);
  private readonly realtime = inject(RealtimeService);
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  private readonly load$ = new Subject<void>();

  readonly states = STATES;
  readonly pageSizes = [15, 25, 50];
  readonly master = computed(() => this.auth.user()?.account_type === 'master_agent');
  readonly filter = signal<Filter | null>(null);
  readonly search = signal('');
  readonly page = signal(1);
  readonly pageSize = signal(15);
  readonly rows = signal<MyUserRow[] | null>(null);
  readonly total = signal(0);
  readonly summary = signal<MyUsersSummary | null>(null);
  readonly error = signal<string | null>(null);
  readonly busy = signal<string | null>(null);
  /** Inline commission editor for one direct agent (master agents only). */
  readonly editing = signal<MyUserRow | null>(null);
  readonly rateDraft = signal('');
  readonly allocation = signal<number | null>(null);
  readonly wallet = signal<string | null>(null);
  readonly transfers = signal<BalanceTransfer[] | null>(null);
  readonly transferTarget = signal<MyUserRow | null>(null);
  readonly preview = signal<TransferPreview | null>(null);
  readonly amount = signal('');
  readonly transferError = signal<string | null>(null);
  private transferKey: string | null = null;

  readonly pages = computed(() => Math.max(1, Math.ceil(this.total() / this.pageSize())));
  readonly range = computed(() => {
    const total = this.total();
    if (!total) return '0 of 0';
    const start = (this.page() - 1) * this.pageSize() + 1;
    return `${start}–${Math.min(total, start + this.pageSize() - 1)} of ${total}`;
  });
  readonly keeps = computed(() => {
    const total = this.allocation();
    const draft = Number(this.rateDraft());
    if (total === null || this.rateDraft().trim() === '' || !Number.isFinite(draft) || draft < 0 || draft > total + 1e-9) return null;
    return Math.round((total - draft) * 100) / 100;
  });

  constructor() {
    this.load$.pipe(
      debounceTime(150),
      switchMap(() => {
        const f = this.filter();
        return forkJoin({
          list: this.api.list({ page: this.page(), page_size: this.pageSize(), kind: f?.kind, state: f?.state, q: this.search().trim() || null })
            .pipe(map((value) => ({ ok: true as const, value })), catchError((e: unknown) => of({ ok: false as const, message: toApiError(e).message }))),
          summary: this.api.summary().pipe(catchError(() => of(null))),
        });
      }),
      takeUntilDestroyed(),
    ).subscribe(({ list, summary }) => {
      if (list.ok) { this.rows.set(list.value.items); this.total.set(list.value.total); this.error.set(null); }
      else this.error.set(list.message);
      if (summary) this.summary.set(summary);
    });
    this.reload();
    this.loadWallet();
    this.loadTransfers();
    if (this.master()) {
      this.commissions.overview().pipe(takeUntilDestroyed()).subscribe({ next: (o) => this.allocation.set(Number(o.allocation ?? o.rate) * 100), error: () => undefined });
    }
    this.realtime.connect();
    this.realtime.on('commission.updated').pipe(takeUntilDestroyed()).subscribe(() => this.reload());
    this.realtime.on('wallet.updated', 'balance.transferred').pipe(takeUntilDestroyed()).subscribe(() => {
      this.loadWallet();
      this.loadTransfers();
      this.reload();
    });
  }

  loadWallet(): void {
    this.api.wallet().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: (w) => this.wallet.set(w.balance), error: () => undefined });
  }

  loadTransfers(): void {
    this.api.transfers({ page: 1, page_size: 8 }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (page) => this.transfers.set(page.items),
      error: () => this.transfers.set([]),
    });
  }

  canFund(row: MyUserRow): boolean {
    if (row.status !== 'active') return false;
    if (row.account_type === 'player') return true;
    return this.master() && row.account_type === 'agent';
  }

  openTransfer(row: MyUserRow): void {
    this.transferTarget.set(row);
    this.amount.set('');
    this.transferError.set(null);
    this.preview.set(null);
    this.transferKey = newIdempotencyKey('transfer');
    this.api.preview(row.id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (view) => { this.preview.set(view); this.wallet.set(view.sender_balance); },
      error: (e: unknown) => this.transferError.set(toApiError(e).message),
    });
  }

  closeTransfer(): void {
    if (this.busy()) return;
    this.transferTarget.set(null);
    this.preview.set(null);
    this.transferError.set(null);
  }

  setAmount(value: string): void {
    if (value.trim() !== this.amount().trim() && !this.busy()) this.transferKey = newIdempotencyKey('transfer');
    this.amount.set(value);
  }

  confirmTransfer(event: Event): void {
    event.preventDefault();
    const row = this.transferTarget();
    const amount = this.amount().trim();
    if (!row || this.busy()) return;
    if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0) {
      this.transferError.set('Enter an amount above zero.');
      return;
    }
    this.busy.set(row.id);
    this.transferError.set(null);
    const key = this.transferKey ?? newIdempotencyKey('transfer');
    this.transferKey = key;
    this.api.transfer(row.id, { amount: Number(amount).toFixed(2), idempotency_key: key }).subscribe({
      next: (result) => {
        this.busy.set(null);
        this.transferTarget.set(null);
        this.wallet.set(result.sender_balance_after);
        this.rows.update((list) => list?.map((item) => item.id === row.id ? { ...item, balance: result.player_balance_after } : item) ?? list);
        this.toast.success(`${result.reference}: ${result.amount} added to ${result.player_username}.`);
        this.loadTransfers();
      },
      error: (e: unknown) => { this.busy.set(null); this.transferError.set(toApiError(e).message); },
    });
  }

  reload(): void { this.load$.next(); }

  choose(kind: MyUserKind, state: MyUserState): void {
    const current = this.filter();
    this.filter.set(current?.kind === kind && current.state === state ? null : { kind, state });
    this.page.set(1);
    this.reload();
  }

  isChosen(kind: MyUserKind, state: MyUserState): boolean {
    const f = this.filter();
    return !!f && f.kind === kind && f.state === state;
  }

  count(kind: MyUserKind, state: MyUserState): number | null {
    const s = this.summary();
    const bucket = kind === 'player' ? s?.players : s?.agents;
    return bucket ? bucket[state] : null;
  }

  onSearch(term: string): void { this.search.set(term); this.page.set(1); this.reload(); }
  goTo(page: number): void { this.page.set(Math.min(Math.max(1, page), this.pages())); this.reload(); }
  setPageSize(size: string): void { this.pageSize.set(Number(size)); this.page.set(1); this.reload(); }

  pct(rate: string | null): string { return rate === null ? '—' : `${(Number(rate) * 100).toFixed(2)}%`; }
  role(row: MyUserRow): string { return row.account_type === 'agent' ? 'Agent' : 'Player'; }
  canSetRate(row: MyUserRow): boolean { return this.master() && row.account_type === 'agent' && row.direct; }

  editRate(row: MyUserRow): void {
    this.editing.set(row);
    this.rateDraft.set(row.rate === null ? '0' : (Number(row.rate) * 100).toFixed(2));
  }

  saveRate(): void {
    const row = this.editing();
    if (!row) return;
    if (this.keeps() === null) {
      this.toast.error(`Enter a rate from 0% to ${this.allocation()?.toFixed(2) ?? '—'}%.`);
      return;
    }
    this.busy.set(row.id);
    this.commissions.setAgentRate(row.id, (Number(this.rateDraft()) / 100).toFixed(4)).subscribe({
      next: (saved) => {
        this.busy.set(null);
        this.editing.set(null);
        this.toast.success(`${saved.username}: ${this.pct(saved.agent_rate)} agent, ${this.pct(saved.master_rate)} yours. Applies to new bets.`);
        this.reload();
      },
      error: (e: unknown) => { this.busy.set(null); this.toast.error(toApiError(e).message); },
    });
  }

  toggleBlock(row: MyUserRow): void {
    if (row.status === 'banned') return;
    const blocking = row.status === 'active';
    this.busy.set(row.id);
    (blocking ? this.api.block(row.id) : this.api.unblock(row.id)).subscribe({
      next: () => {
        this.busy.set(null);
        this.toast.success(`${row.username} ${blocking ? 'blocked. They are signed out and cannot sign in.' : 'unblocked.'}`);
        this.reload();
      },
      error: (e: unknown) => { this.busy.set(null); this.toast.error(toApiError(e).message); },
    });
  }
}

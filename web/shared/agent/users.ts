import { ActivatedRoute } from '@angular/router';
import { CreateDownlineDialog } from './create-downline-dialog';
import { agentShareLinks } from './links';
import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { MatDialog } from '@angular/material/dialog';
import { Subject, catchError, debounceTime, firstValueFrom, forkJoin, map, of, switchMap } from 'rxjs';

import { LoadingDialog } from './loading-dialog';

import type { TenantProfile } from '@shared/models';
import { ArenaPointsPipe } from '@shared/pipes';
import {
  AgentUsersApi, AuthStore, CommissionApi, MyUserKind, MyUserRow, MyUserState, MyUsersSummary, PlayerActivityRow, RateHistoryRow, RealtimeService, ToastService, toApiError,
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
 * Loading form moves play balance with the selected user already chosen.
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
  private readonly commissions = inject(CommissionApi);
  private readonly dialog = inject(MatDialog);
  private readonly toast = inject(ToastService);
  private readonly realtime = inject(RealtimeService);
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  private readonly load$ = new Subject<void>();

  readonly states = STATES;
  readonly pageSizes = [15, 25, 50];
  private readonly route = inject(ActivatedRoute, {optional:true});
  readonly subAgentPage = this.route?.snapshot.data['kind'] === 'sub_agent';
  readonly manager = computed(() => ['master_agent','agent'].includes(this.auth.user()?.account_type ?? ''));
  readonly role = computed(() => this.auth.user()?.account_type);
  readonly viewParent = signal<MyUserRow|null>(null);
  readonly activity = signal<PlayerActivityRow[]|null>(null);
  readonly activityPlayer = signal<string|null>(null);
  readonly sharingHistory = signal<RateHistoryRow[]|null>(null);
  readonly inviteLink = computed(() => agentShareLinks(globalThis.location?.origin ?? '', this.auth.user()?.username ?? '', this.role()).application);
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
    if (total === null || !/^\d+(\.\d{1,2})?$/.test(this.rateDraft()) || !Number.isFinite(draft) || draft < 0 || draft > total + 1e-9) return null;
    return Math.round((total - draft) * 100) / 100;
  });

  constructor() {
    this.load$.pipe(
      debounceTime(150),
      switchMap(() => {
        const f = this.filter();
        return forkJoin({
          list: this.api.list({ page: this.page(), page_size: this.pageSize(), kind: this.viewParent() ? 'player' : this.subAgentPage ? 'sub_agent' : f?.kind, parent_id: this.viewParent()?.id, state: f?.state, q: this.search().trim() || null })
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
    if (this.manager()) {
      this.commissions.overview().pipe(takeUntilDestroyed()).subscribe({ next: (o) => this.allocation.set(Number(o.allocation ?? o.rate) * 100), error: () => undefined });
    }
    this.realtime.connect();
    this.realtime.on('commission.updated', 'users.updated', 'connected').pipe(takeUntilDestroyed()).subscribe(() => {
      this.reload();
      if (this.manager()) this.commissions.overview().subscribe({next:o=>this.allocation.set(Number(o.rate)*100),error:()=>undefined});
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
    const bucket = kind === 'player' ? s?.players : kind === 'sub_agent' ? s?.sub_agents : s?.agents;
    return bucket ? bucket[state] : null;
  }

  onSearch(term: string): void { this.search.set(term); this.page.set(1); this.reload(); }
  goTo(page: number): void { this.page.set(Math.min(Math.max(1, page), this.pages())); this.reload(); }
  setPageSize(size: string): void { this.pageSize.set(Number(size)); this.page.set(1); this.reload(); }

  pct(rate: string | null): string { return rate === null ? '—' : `${(Number(rate) * 100).toFixed(2)}%`; }
  roleLabel(row: MyUserRow): string { return row.account_type === 'sub_agent' ? 'Sub-Agent' : row.account_type === 'agent' ? 'Agent' : 'Player'; }
  canSetRate(row: MyUserRow): boolean { return row.direct && (this.master() && row.account_type === 'agent' || this.role() === 'agent' && row.account_type === 'sub_agent'); }

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

  async createAccount(): Promise<void> {
    const ref = this.dialog.open(CreateDownlineDialog,{data:this.subAgentPage ? 'sub_agent' : 'player',width:'460px',maxWidth:'95vw'});
    if(await firstValueFrom(ref.afterClosed())) this.reload();
  }
  showActivity(row: MyUserRow):void {
    this.activityPlayer.set(row.username);
    this.api.activity(row.id).subscribe({next:r=>this.activity.set(r.items),error:e=>this.toast.error(e)});
  }
  showSharingHistory():void {
    this.commissions.rateHistory().subscribe({next:r=>this.sharingHistory.set(r.items),error:e=>this.toast.error(e)});
  }
  sharingDetails(row: RateHistoryRow):string {
    const change=(row.changes['agent_rate'] ?? row.changes['allocation']) as {from?:string;to?:string}|undefined;
    const text=change ? `${this.pct(change.from ?? null)} → ${this.pct(change.to ?? null)}` : 'Allocation updated';
    const remainder=row.changes['master_rate'];
    return remainder ? `${text}. Upline retains ${this.pct(String(remainder))}.` : text;
  }
  showPlayers(row:MyUserRow|null):void {this.viewParent.set(row);this.page.set(1);this.reload();}
  retainedRate(row:MyUserRow):string {return row.rate === null || this.allocation() === null ? '—' : `${(this.allocation()!-Number(row.rate)*100).toFixed(2)}%`;}


  async openLoading(row: MyUserRow): Promise<void> {
    const overview = await firstValueFrom(this.commissions.overview().pipe(catchError(() => of(null))));
    const ref = this.dialog.open(LoadingDialog, {
      data: { balance: overview?.own_balance ?? '0.00', recipient: row },
      width: '460px',
      maxWidth: '95vw',
      autoFocus: 'dialog',
    });
    if (await firstValueFrom(ref.afterClosed())) this.reload();
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

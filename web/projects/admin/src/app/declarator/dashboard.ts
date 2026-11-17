import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { firstValueFrom } from 'rxjs';
import { VideoStageComponent, derbyStats } from '@shared/arena';
import { CellDefDirective, Column, ConfirmService, DataTableComponent, EmptyStateComponent, ErrorStateComponent, StatusChipComponent } from '@shared/components';
import type { FightBookSummary, Match } from '@shared/models';
import { ArenaPointsPipe } from '@shared/pipes';
import { AppVersion, DECLARATION_LABELS, DeclarationOutcome, MatchRecord, WORKFLOW_LABELS, toApiError } from '@shared/services';
import { exactCents, poolBoardOdds, quickBetLabel } from '@shared/services';
import { GhostClick, GhostOutcome, GhostQueue } from './ghost-queue';
import { DeclarationHistoryDialog } from './history-dialog';
import { HistoryBeadsComponent } from './history-beads';
import { DeclaratorStore } from './store';

/** Manual ghost requests in flight at once until the API states its own bound (the monitor's
 *  `ghost_concurrency`, a deployment setting sized to the API's CPU). Ghost writes do not lock the event
 *  row, so concurrent clicks run concurrently on the server; beyond the API's CPU they only queue there.
 *  Clicks beyond the bound wait in the desk, still in click order. */
export const GHOST_CONCURRENCY = 6;
/** Resends of a click whose answer was lost (same request id, so never added twice). */
export const GHOST_RETRIES = 2;

/** The question every declaration is confirmed with, e.g. "...declare WALA as the winner of Fight #135?" */
export function confirmQuestion(outcome: DeclarationOutcome, fight: number): string {
  switch (outcome) {
    case 'meron': case 'wala': return `Are you sure you want to declare ${outcome.toUpperCase()} as the winner of Fight #${fight}?`;
    case 'draw': return `Are you sure you want to declare Fight #${fight} a DRAW?`;
    case 'no_contest': return `Are you sure you want to declare Fight #${fight} NO CONTEST?`;
    case 'cancelled': return `Are you sure you want to cancel Fight #${fight}? Every prediction on this fight becomes void.`;
  }
}

export interface ActivityRow {
  key: string; no: string; username: string; side: 'meron' | 'wala' | 'draw'; amount: string; ghost: boolean;
  /** Profit rate if this side wins, e.g. "83%" at a 1.83 payout. Null when the row is never paid. */
  winPercent: string | null;
  /** Points returned if this side wins, stake included. Null when the row is never paid. */
  possibleWin: string | null;
}

/** Profit percentage of a payout multiplier: 1.83 -> "83%", 8 -> "700%". */
export function profitPercent(multiplier: string | number | null | undefined): string | null {
  if (multiplier === null || multiplier === undefined || multiplier === '') return null;
  const text = String(multiplier);
  const [whole, frac = ''] = text.split('.');
  if (!/^\d+$/.test(whole) || !/^\d*$/.test(frac)) return null;
  const scale = 10 ** frac.length;
  const units = Number(whole) * scale + Number(frac || 0);
  if (units <= 0) return null;
  const profit = units - scale;
  const hundredths = Math.round((profit * 10000) / scale);
  const body = Math.trunc(hundredths / 100);
  const remainder = Math.abs(hundredths % 100);
  return remainder === 0 ? `${body}%` : `${body}.${String(remainder).padStart(2, '0').replace(/0$/, '')}%`;
}

/** Gross return in cents, half up, matching settlement. */
function paysCents(amount: string, multiplier: string | number): number | null {
  const stake = Math.round(Number(amount) * 100);
  const text = String(multiplier);
  const [whole, frac = ''] = text.split('.');
  if (!Number.isFinite(stake) || stake <= 0 || !/^\d+$/.test(whole) || !/^\d*$/.test(frac)) return null;
  const scale = 10 ** frac.length;
  const units = Number(whole) * scale + Number(frac || 0);
  if (units <= 0) return null;
  return Math.round((stake * units) / scale);
}

/** Why a fight was cancelled: the official record first, then the pending proposal. */
export function cancellationReason(record: MatchRecord): string | null {
  if (record.official_winner !== 'cancelled' && record.declaration?.outcome !== 'cancelled') return null;
  return record.match.cancel_reason || record.declaration?.reason || null;
}

@Component({
  selector: 'app-declarator-dashboard',
  imports: [DatePipe, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule, MatSelectModule,
    ArenaPointsPipe, VideoStageComponent, HistoryBeadsComponent, DataTableComponent, CellDefDirective, EmptyStateComponent, ErrorStateComponent, StatusChipComponent],
  providers: [DeclaratorStore],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './dashboard.scss',
  templateUrl: './dashboard.html',
})
export class DeclaratorDashboard {
  readonly store = inject(DeclaratorStore);
  private readonly confirm = inject(ConfirmService);
  private readonly dialog = inject(MatDialog);
  readonly busy = signal(false);
  readonly dummySides = ['meron', 'wala'] as const;
  private readonly quickAmounts = computed(() => Object.fromEntries(this.dummySides.map(side =>
    [side, this.store.quickBets()[side].map(amount => ({value: Number(amount), label: quickBetLabel(amount)}))],
  )) as Record<'meron' | 'wala', {value: number; label: string}[]>);
  dummyAmounts(side: 'meron' | 'wala') { return this.quickAmounts()[side]; }
  readonly version = inject(AppVersion);
  /** Manual ghost clicks: dispatched in click order, up to GHOST_CONCURRENCY at a time, each with its own
   *  idempotency key. Stop and Close go first. Each accepted answer updates the book directly. */
  readonly ghostQueue = new GhostQueue({
    send: click => this.sendGhost(click),
    settled: (click, outcome, error) => this.ghostSettled(click, outcome, error),
  }, GHOST_CONCURRENCY);

  /** One click. A lost connection or a gateway error leaves its outcome unknown, so it is resent with the
   *  same request id (the server adds it at most once and answers a repeat with the book), up to twice. */
  private async sendGhost(click: GhostClick): Promise<void> {
    const body = { request_id: click.requestId, side: click.side, amount: click.amount.toFixed(2) };
    let book: FightBookSummary;
    for (let attempt = 0; ; attempt++) {
      try {
        book = await firstValueFrom(this.store.api.addDummyBet(click.matchId, body));
        break;
      } catch (error) {
        const { status, code } = toApiError(error);
        const unanswered = code === 'network_error' || status === 502 || status === 503 || status === 504;
        if (attempt >= GHOST_RETRIES || !unanswered) throw error;
        await new Promise(resolve => setTimeout(resolve, 250 * (attempt + 1)));
      }
    }
    // Accepted. Showing it can never turn the click into a refusal: if the answer cannot be applied
    // to the desk's book, the desk reloads the book instead.
    try { if (!this.store.applyBookFrame(book)) this.store.refreshLive(); } catch { this.store.refreshLive(); }
  }
  /** Clicks the server has not decided yet (queued plus sending). */
  readonly dummyPending = computed(() => this.ghostQueue.queued() + this.ghostQueue.inFlight());
  /** New manual ghost clicks are refused while Stop/Close runs, the queue is full, or the desk must reload. */
  /** This fight's manual ghost clicks by outcome; every click is counted exactly once. */
  readonly ghostTally = signal({ added: 0, refused: 0, cancelled: 0 });
  /** The latest cancellation report (Stop/Close). Refusals never overwrite it. */
  readonly ghostNotice = signal<string | null>(null);
  readonly ghostBlocked = computed(() =>
    this.ghostQueue.stopped() || this.dummyPending() >= this.ghostQueue.capacity || this.version.required());

  private readonly projectionMatchId = computed(() => this.store.currentBook?.()?.match_id);
  constructor() {
    effect(() => {
      const id = this.projectionMatchId();
      untracked(() => {
        this.projected.update(values => Object.fromEntries(
          Object.entries(values).filter(([key]) => id && key.startsWith(`${id}:`)),
        ));
        this.activityPage.set(1);
        this.ghostTally.set({ added: 0, refused: 0, cancelled: 0 });
        this.ghostNotice.set(null);
      });
    });
    effect(() => {
      this.store.eventId?.();
      untracked(() => { this.onlinePage.set(1); this.settlementPage.set(1); });
    });
    // The API's bound on concurrent ghost requests (absent on an older API: keep the default).
    effect(() => {
      const limit = this.store.monitor?.()?.ghost_concurrency;
      if (limit) untracked(() => this.ghostQueue.setLimit(limit));
    });
    // An outdated desk sends nothing more: unsent ghost clicks are cancelled and reported.
    effect(() => {
      if (this.version.required()) untracked(() => this.reportCancelled(this.ghostQueue.halt(), 'this desk must reload'));
    });
    this.version.watch();
  }
  /** Optimistic lower bounds per fight/side. Server snapshots may already contain our clicks. */
  private readonly projected = signal<Record<string, number>>({});
  totalPool(b: FightBookSummary, side: 'meron' | 'wala'): string {
    const extra = Number(this.dummyPool(b, side)) - Number(b[`dummy_${side}`] ?? 0);
    return (Number(b.pools[side]) + extra).toFixed(2);
  }
  readonly actionError = signal<string | null>(null);
  readonly dummyError = signal<string | null>(null);
  readonly message = signal('');
  readonly labels = DECLARATION_LABELS;
  readonly statuses = WORKFLOW_LABELS;
  readonly resultLabel = (value: DeclarationOutcome) => this.labels[value];
  readonly multiplier = (value: string) => String(Number(value));
  /** Dummy points on one side of a book ("0.00" when none). */
  dummyPool(b: FightBookSummary, side: 'meron' | 'wala'): string {
    return (Math.max(Math.round(Number(b[`dummy_${side}`] ?? 0) * 100), this.projected()[`${b.match_id}:${side}`] ?? 0) / 100).toFixed(2);
  }
  /** Real player bets on one side: the shown pool minus the Declarator's dummy points. */
  playerPool(b: FightBookSummary, side: 'meron' | 'wala'): string {
    const cents = Math.round(Number(b.pools[side] ?? 0) * 100) - Math.round(Number(b[`dummy_${side}`] ?? 0) * 100);
    return (Math.max(0, cents) / 100).toFixed(2);
  }
  /** Ghost points this desk has shown on a side ahead of the server (clicks not yet in the book). */
  private aheadCents(b: FightBookSummary, side: 'meron' | 'wala'): number {
    return Math.max(0, (this.projected()[`${b.match_id}:${side}`] ?? 0) - Math.round(Number(b[`dummy_${side}`] ?? 0) * 100));
  }
  /** The Payout cells and whether they are projected.
   *
   *  With no unconfirmed ghost click, both prices are the server's board price. While this desk shows
   *  ghost points the server has not confirmed yet, a pool-priced fight shows the price those totals give,
   *  computed with the API's own formula (`poolBoardOdds`, checked against the same cases as the API), so
   *  both prices move on the click itself. A manual pair or Owner-set odds do not move with the pools,
   *  so they stay as they are. A projection is display only: the server's answer or its next
   *  `book.updated` replaces it, and MERON/WALA are paid at the board price when betting closes. */
  readonly payout = computed(() => {
    const b = this.store.currentBook();
    if (!b) return null;
    const ahead = b.state === 'open' && this.dummySides.some(side => this.aheadCents(b, side) > 0);
    const server = { meron: b.odds.meron_display ?? null, wala: b.odds.wala_display ?? null };
    if (!ahead) return { ...server, projected: false, totalsProjected: false };
    if (b.odds.source !== 'pool') return { ...server, projected: false, totalsProjected: true };
    return { ...poolBoardOdds(exactCents(this.totalPool(b, 'meron')), exactCents(this.totalPool(b, 'wala'))),
      projected: true, totalsProjected: true };
  });
  hasDummy(b: FightBookSummary): boolean {
    return Number(b.dummy_meron ?? 0) > 0 || Number(b.dummy_wala ?? 0) > 0;
  }
  /** Payout multiplier for a side, from the book. Meron and Wala use the displayed odds; Draw uses its locked multiplier. */
  private sideMultiplier(b: FightBookSummary, side: 'meron' | 'wala' | 'draw'): string | null {
    const shown = this.payout();
    const raw = side === 'draw' ? b.draw_multiplier : side === 'meron' ? shown?.meron ?? b.odds.meron_display : shown?.wala ?? b.odds.wala_display;
    return raw == null || raw === '' ? null : String(raw);
  }
  /** One row per player and side on the current fight, ghost (simulated) rows first and marked "*". */
  readonly activity = computed<ActivityRow[]>(() => {
    const b = this.store.currentBook();
    if (!b) return [];
    const rows: ActivityRow[] = [];
    const paid = (side: 'meron' | 'wala' | 'draw', amount: string, ghost: boolean) => {
      if (ghost) return { winPercent: null, possibleWin: null };
      const multiplier = this.sideMultiplier(b, side);
      const cents = multiplier == null ? null : paysCents(amount, multiplier);
      return {
        winPercent: profitPercent(multiplier),
        possibleWin: cents == null ? null : (cents / 100).toFixed(2),
      };
    };
    for (const side of this.dummySides) {
      const amount = this.dummyPool(b, side);
      if (Number(amount) > 0) rows.push({ key: `ghost:${side}`, no: '*', username: 'Ghost bet', side, amount, ghost: true, ...paid(side, amount, true) });
    }
    let n = 0;
    for (const p of b.players ?? []) {
      for (const side of ['meron', 'wala', 'draw'] as const) {
        if (Number(p[side] ?? 0) > 0) rows.push({ key: `${p.username}:${side}`, no: String(++n), username: p.username, side, amount: p[side], ghost: false, ...paid(side, p[side], false) });
      }
    }
    return rows;
  });

  /** Connected players (name, remaining balance, stake on the current fight), filtered by the search box. */
  readonly onlineSearch = signal('');
  readonly onlineRows = computed(() => {
    const term = this.onlineSearch().trim().toLowerCase();
    const items = this.store.onlineList()?.items ?? [];
    return (term ? items.filter(p => p.username.toLowerCase().includes(term) || p.full_name.toLowerCase().includes(term)) : items)
      .map(p => ({ ...p, stakes: this.dummySidesAndDraw.filter(side => Number(p[`${side}_stake`]) > 0).map(side => ({ side, amount: p[`${side}_stake`] })) }));
  });
  private readonly dummySidesAndDraw = ['meron', 'wala', 'draw'] as const;

  // ---- desk controls ----
  /** Actions in flight, so each control disables itself and not the whole desk. */
  private readonly running = signal<ReadonlySet<string>>(new Set());
  deskBusy(action: string): boolean { return this.running().has(action); }
  readonly jumpNumber = signal('');
  readonly ghostMeron = signal('1000');
  readonly ghostWala = signal('1000');
  readonly ghostInterval = signal('5');
  digits(event: Event): string {
    const input = event.target as HTMLInputElement;
    input.value = input.value.replace(/\D/g, '').slice(0, 6);
    return input.value;
  }
  decimal(event: Event): string {
    const input = event.target as HTMLInputElement;
    input.value = input.value.replace(/[^\d.]/g, '').replace(/(\..*)\./g, '$1');
    return input.value;
  }

  private async run(action: string, work: () => Promise<void>): Promise<void> {
    if (this.busy() || this.running().size > 0 || this.version.required()) return;
    this.running.update(s => new Set(s).add(action));
    this.actionError.set(null);
    try { await work(); } catch (error) { this.actionError.set(toApiError(error).message); this.store.reconcile(); }
    finally { this.running.update(s => { const next = new Set(s); next.delete(action); return next; }); }
  }

  async endEvent(): Promise<void> {
    const event = this.store.monitor()?.event;
    if (!event || !this.store.submitter()) return;
    const ok = await this.confirm.ask({ title: 'End event', message: `End "${event.name}"? Declare or cancel the fight in progress first. Auto ghost betting stops and no more fights can be added.`,
      confirmText: 'End event', cancelText: 'Keep event live', destructive: true });
    if (ok === null) return;
    await this.run('end', async () => {
      await firstValueFrom(this.store.api.endEvent(event.id));
      this.message.set(`${event.name} has ended.`);
      this.store.refresh();
    });
  }

  /** Reload this desk, and (for the Declarator) tell every connected player screen to reload the event. */
  async refreshAll(): Promise<void> {
    const id = this.store.eventId();
    this.reconnect();
    if (!id || !this.store.submitter()) return;
    await this.run('refresh', async () => {
      await firstValueFrom(this.store.api.refreshAll(id));
      this.message.set('Refresh sent to every connected screen.');
    });
  }

  async jump(event?: Event): Promise<void> {
    event?.preventDefault();
    const id = this.store.eventId();
    const number = Number(this.jumpNumber());
    if (!id || !this.store.submitter() || !Number.isInteger(number) || number < 1) return;
    await this.run('jump', async () => {
      const fight = await firstValueFrom(this.store.api.jumpToFight(id, number));
      this.message.set(`Next fight set to #${fight.match_number}.`);
      this.jumpNumber.set('');
      this.store.reconcile();
    });
  }

  async startGhost(event?: Event): Promise<void> {
    event?.preventDefault();
    const id = this.store.eventId();
    const meron = Number(this.ghostMeron() || 0), wala = Number(this.ghostWala() || 0), every = Number(this.ghostInterval());
    if (!id || !this.store.submitter() || this.store.ghost()?.active) return;
    if (!(meron > 0 || wala > 0)) { this.actionError.set('Enter a Meron or Wala ghost amount above zero.'); return; }
    if (!Number.isInteger(every) || every < 1 || every > 3600) { this.actionError.set('Enter an interval from 1 to 3600 seconds.'); return; }
    await this.run('ghost', async () => {
      const ghost = await firstValueFrom(this.store.api.startGhostAuto(id, { meron_amount: meron.toFixed(2), wala_amount: wala.toFixed(2), interval_seconds: every }));
      this.store.ghost.set(ghost);
      this.message.set(`Auto ghost betting started on Fight #${ghost.match_number}: Meron +${meron.toFixed(2)} · Wala +${wala.toFixed(2)} every ${every}s. No balance changes.`);
    });
  }

  /** Stop comes first: it is never refused because another desk action runs, new manual ghost clicks
   *  are blocked at once, unsent ones are cancelled and reported, and the request goes out immediately.
   *  Clicks already sent finish; the server decides them. */
  async stopGhost(): Promise<void> {
    const id = this.store.eventId();
    if (!id || !this.store.submitter() || this.running().has('ghost')) return;
    this.reportCancelled(this.ghostQueue.halt(), 'Stop');
    this.running.update(s => new Set(s).add('ghost'));
    this.actionError.set(null);
    try {
      this.store.ghost.set(await firstValueFrom(this.store.api.stopGhostAuto(id)));
      this.message.set('Auto ghost betting stopped.');
    } catch (error) {
      this.actionError.set(toApiError(error).message);
      this.store.reconcile();
    } finally {
      this.running.update(s => { const next = new Set(s); next.delete('ghost'); return next; });
      // Betting is still open after a Stop, so manual ghost clicks are allowed again.
      if (!this.version.required() && this.store.currentBook()?.state === 'open') this.ghostQueue.resume();
    }
  }

  /** Betting state of a fight, as players see it. */
  bookState(b: FightBookSummary): string {
    if (b.state === 'settled') return `Settled · ${this.labels[b.outcome as DeclarationOutcome] ?? b.outcome}`;
    if (b.state === 'open') return b.accepting ? 'Betting open' : 'Open · not accepting';
    return 'Betting closed';
  }
  readonly stateLabel = (value: keyof typeof WORKFLOW_LABELS) => this.statuses[value];
  readonly outcomes: DeclarationOutcome[] = ['meron', 'wala', 'draw', 'no_contest', 'cancelled'];
  readonly states = ['pending', 'pending_approval', 'under_review', 'finalized', 'cancelled'] as const;
  readonly summary = computed(() => derbyStats(this.store.monitor()?.matches ?? []));
  readonly activityPage = signal(1);
  readonly onlinePage = signal(1);
  readonly settlementPage = signal(1);
  readonly pageSize = 25;
  readonly visibleActivity = computed(() => this.pageRows(this.activity(), this.activityPage()));
  readonly visibleOnline = computed(() => this.pageRows(this.onlineRows(), this.onlinePage()));
  readonly visibleSettlements = computed(() => this.pageRows(this.store.settlements(), this.settlementPage()));
  private pageRows<T>(rows: T[], page: number): T[] {
    const start = Math.min(page - 1, Math.max(0, Math.ceil(rows.length / this.pageSize) - 1)) * this.pageSize;
    return rows.slice(start, start + this.pageSize);
  }
  pageCount(count: number): number { return Math.max(1, Math.ceil(count / this.pageSize)); }
  private readonly requestKeys = new Map<string, string>();
  readonly columns: Column<MatchRecord>[] = [
    { key: 'fight', label: 'Fight', cell: r => '#' + r.match.match_number },
    { key: 'event', label: 'Event', cell: r => r.event_name, hideOnMobile: true },
    { key: 'winner', label: 'Official winner' },
    { key: 'fight_status', label: 'Fight status', cell: r => r.match.status.replaceAll('_', ' '), hideOnMobile: true },
    { key: 'status', label: 'Status' },
    { key: 'declared_by', label: 'Declared by', cell: r => r.declared_by ?? '—', hideOnMobile: true },
    { key: 'declared_at', label: 'Declared at', hideOnMobile: true },
    { key: 'finalized_at', label: 'Finalized at', hideOnMobile: true },
    { key: 'reason', label: 'Cancellation reason', cell: r => cancellationReason(r) ?? '—', hideOnMobile: true },
  ];

  async declare(outcome: DeclarationOutcome): Promise<void> {
    const record = this.store.current();
    if (!record || !this.store.canSubmit() || this.busy() || this.running().size > 0) return;
    this.busy.set(true);
    this.actionError.set(null);
    try {
      const reason = await this.confirm.ask({ title: outcome === 'cancelled' ? `Cancel Fight #${record.match.match_number}` : `Fight #${record.match.match_number}`,
        message: `${confirmQuestion(outcome, record.match.match_number)} This is the official result: it is published and settled immediately.`,
        confirmText: outcome === 'cancelled' ? 'Cancel fight' : 'Confirm declaration', cancelText: outcome === 'cancelled' ? 'Keep fight' : 'Go back',
        destructive: outcome === 'cancelled',
        ...(outcome === 'cancelled' ? { inputLabel: 'Cancellation reason', inputRequired: true, inputMinLength: 3 } : {}),
      });
      if (reason === null) return;
      const key = `${record.match.id}:${outcome}:${reason}`;
      const request = this.requestKeys.get(key) ?? crypto.randomUUID();
      this.requestKeys.set(key, request);
      if (this.requestKeys.size > 32) this.requestKeys.delete(this.requestKeys.keys().next().value!);
      const declaration = await firstValueFrom(this.store.api.submit(record.match.id, { request_id: request, outcome, base_revision: 0, reason: reason || null }));
      this.requestKeys.delete(key);
      this.message.set(`Fight #${record.match.match_number}: ${declaration.status !== 'finalized' ? this.statuses[declaration.status] + '.' : outcome === 'cancelled' ? 'cancelled (official).' : this.labels[outcome] + ' is the official result.'}`);
      // The card, book and records change; the event list does not.
      this.store.reconcile();
    } catch (error) { this.actionError.set(toApiError(error).message); this.store.reconcile(); }
    finally { this.busy.set(false); }
  }

  /** Project each click immediately; max(server, projection) avoids counting WebSocket echoes twice.
   *  The click joins the queue (sent at once while fewer than GHOST_CONCURRENCY are in flight); the
   *  server accepts or refuses it. */
  addDummy(side: 'meron' | 'wala', amount: number): void {
    const book = this.store.currentBook();
    if (!book || book.state !== 'open' || this.busy() || this.ghostBlocked() || !this.store.submitter()
      || !this.dummyAmounts(side).some(a => a.value === amount)) return;
    const label = this.dummyAmounts(side).find(a => a.value === amount)!.label;
    const key = `${book.match_id}:${side}`;
    const cents = Math.round(amount * 100);
    const base = Math.round(Number(this.dummyPool(book, side)) * 100);
    if (!this.dummyPending()) this.ghostNotice.set(null);
    // Sending is asynchronous, so the click is always projected before any answer can settle it.
    if (!this.ghostQueue.enqueue({ matchId: book.match_id, side, amount, label, requestId: crypto.randomUUID() })) return;
    this.projected.update(p => ({ ...p, [key]: base + cents }));
    this.dummyError.set(null);
  }

  private ghostSettled(click: GhostClick, outcome: GhostOutcome, error?: unknown): void {
    if (click.matchId === this.projectionMatchId()) {
      const field = outcome === 'accepted' ? 'added' : outcome === 'rejected' ? 'refused' : 'cancelled';
      this.ghostTally.update(t => ({ ...t, [field]: t[field] + 1 }));
    }
    if (outcome === 'accepted') this.message.set(`Dummy ${click.side.toUpperCase()} +${click.label} added.`);
    else {
      const key = `${click.matchId}:${click.side}`, cents = Math.round(click.amount * 100);
      this.projected.update(p => key in p ? ({ ...p, [key]: p[key] - cents }) : p);
      if (outcome === 'rejected') {
        this.dummyError.set(`Ghost ${click.side.toUpperCase()} +${click.label} was not added: ${toApiError(error).message}`);
        // A refusal by the server leaves the book as it is (a close also publishes its own update);
        // only an unanswered request can leave the desk unsure, so that one reloads the book.
        if (toApiError(error).status === 0 || toApiError(error).status >= 500) this.store.refreshLive();
      }
    }
  }

  /** Unsent clicks are cancelled visibly, never dropped silently. */
  private reportCancelled(cancelled: GhostClick[], reason: string): void {
    if (!cancelled.length) return;
    const total = (side: 'meron' | 'wala') => cancelled.filter(c => c.side === side).reduce((s, c) => s + c.amount, 0);
    this.ghostNotice.set(`${cancelled.length} queued ghost ${cancelled.length === 1 ? 'click was' : 'clicks were'} not sent (${reason}): `
      + `MERON +${total('meron').toLocaleString()} · WALA +${total('wala').toLocaleString()} cancelled.`);
  }

  /** Open (or reopen) or close simulated betting on the switch's fight. On an empty card, opening adds the
   *  next fight first. The API enforces every rule again. */
  async setBetting(open: boolean): Promise<void> {
    const control = this.store.betting();
    const eventId = this.store.monitor()?.event.id;
    // Close comes first like Stop: it is not refused because another desk action runs.
    if ((!open && control?.status !== 'open') || !control || !this.store.submitter() || this.busy() || this.version.required()
      || (open && this.running().size > 0) || !(open ? control.can_open : control.can_close)) return;
    if (!control.match_id && !(open && eventId)) return;
    if (!open) this.reportCancelled(this.ghostQueue.halt(), 'Close bet');
    this.busy.set(true);
    this.actionError.set(null);
    let changed = false;
    try {
      const request = !open ? this.store.api.closeBetting(control.match_id!)
        : control.match_id ? this.store.api.openBetting(control.match_id) : this.store.api.openEventBetting(eventId!);
      const next = await firstValueFrom(request);
      changed = true;
      this.message.set(`Fight #${next.match_number ?? control.match_number}: betting ${next.status === 'open' ? 'OPEN' : 'CLOSED'}.`);
      if (!control.match_id) { this.store.reconcile(); return; }
      this.store.applyBetting(next);
    } catch (error) { this.actionError.set(toApiError(error).message); this.store.reconcile(); }
    finally {
      this.busy.set(false);
      // A new open fight takes manual ghost clicks; a failed Close leaves betting open, so they resume too.
      if (!this.version.required() && (open ? changed : !changed)) this.ghostQueue.resume();
    }
  }

  reconnect(): void { if (this.store.liveEnabled()) { this.store.realtime.disconnect(); this.store.realtime.connect(); } this.store.refresh(); }
  async details(record: MatchRecord): Promise<void> {
    const ref = this.dialog.open(DeclarationHistoryDialog, { data: { matchId: record.match.id }, width: '760px', maxWidth: '96vw' });
    if (await firstValueFrom(ref.afterClosed())) this.store.refresh();
  }
  viewQueue(): void {
    this.store.records.setFilter('event_id', null);
    this.store.records.setFilter('status', 'pending_approval');
    this.store.approvalNotice.set('');
  }
  openMatch(match: Match): void { void this.details({ match } as MatchRecord); }
}

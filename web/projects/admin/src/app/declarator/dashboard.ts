import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
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
import { DECLARATION_LABELS, DeclarationOutcome, MatchRecord, WORKFLOW_LABELS, toApiError } from '@shared/services';
import { DeclarationHistoryDialog } from './history-dialog';
import { HistoryBeadsComponent } from './history-beads';
import { DeclaratorStore } from './store';

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
  /** Quick dummy amounts. Each button has its own 1-second cooldown. */
  readonly dummyAmounts = [
    { label: '5K', value: 5_000 },
    { label: '10K', value: 10_000 },
    { label: '20K', value: 20_000 },
    { label: '50K', value: 50_000 },
  ] as const;
  static readonly DUMMY_COOLDOWN_MS = 1000;
  /** Buttons cooling down (or whose request is still in flight), keyed "side:amount". */
  readonly dummyCooling = signal<ReadonlySet<string>>(new Set());
  isCooling(side: 'meron' | 'wala', amount: number): boolean { return this.dummyCooling().has(`${side}:${amount}`); }
  private readonly dummyTimers = new Set<ReturnType<typeof setTimeout>>();
  readonly actionError = signal<string | null>(null);
  readonly dummyError = signal<string | null>(null);
  readonly message = signal('');
  readonly labels = DECLARATION_LABELS;
  readonly statuses = WORKFLOW_LABELS;
  readonly resultLabel = (value: DeclarationOutcome) => this.labels[value];
  readonly multiplier = (value: string) => String(Number(value));
  /** Dummy points on one side of a book ("0.00" when none). */
  dummyPool(b: FightBookSummary, side: 'meron' | 'wala'): string {
    return Number((side === 'meron' ? b.dummy_meron : b.dummy_wala) ?? 0).toFixed(2);
  }
  /** Real player bets on one side: the shown pool minus the Declarator's dummy points. */
  playerPool(b: FightBookSummary, side: 'meron' | 'wala'): string {
    const cents = Math.round(Number(b.pools[side] ?? 0) * 100) - Math.round(Number(this.dummyPool(b, side)) * 100);
    return (Math.max(0, cents) / 100).toFixed(2);
  }
  hasDummy(b: FightBookSummary): boolean {
    return Number(b.dummy_meron ?? 0) > 0 || Number(b.dummy_wala ?? 0) > 0;
  }
  /** Payout multiplier for a side, from the book. Meron and Wala use the displayed odds; Draw uses its locked multiplier. */
  private sideMultiplier(b: FightBookSummary, side: 'meron' | 'wala' | 'draw'): string | null {
    const raw = side === 'draw' ? b.draw_multiplier : side === 'meron' ? b.odds.meron_display : b.odds.wala_display;
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
    if (this.running().has(action)) return;
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

  async stopGhost(): Promise<void> {
    const id = this.store.eventId();
    if (!id || !this.store.submitter()) return;
    await this.run('ghost', async () => {
      this.store.ghost.set(await firstValueFrom(this.store.api.stopGhostAuto(id)));
      this.message.set('Auto ghost betting stopped.');
    });
  }

  constructor() {
    inject(DestroyRef).onDestroy(() => { this.dummyTimers.forEach(clearTimeout); this.dummyTimers.clear(); });
  }
  private setCooling(key: string, on: boolean): void {
    this.dummyCooling.update(current => {
      const next = new Set(current);
      if (on) next.add(key); else next.delete(key);
      return next;
    });
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
  readonly stats = derbyStats;
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
    if (!record || !this.store.canSubmit() || this.busy()) return;
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
      const declaration = await firstValueFrom(this.store.api.submit(record.match.id, { request_id: request, outcome, base_revision: 0, reason: reason || null }));
      this.requestKeys.delete(key);
      this.message.set(`Fight #${record.match.match_number}: ${declaration.status !== 'finalized' ? this.statuses[declaration.status] + '.' : outcome === 'cancelled' ? 'cancelled (official).' : this.labels[outcome] + ' is the official result.'}`);
      this.store.refresh();
    } catch (error) { this.actionError.set(toApiError(error).message); this.store.reconcile(); }
    finally { this.busy.set(false); }
  }

  /** Add a quick dummy amount to MERON or WALA (one tap, no side toggle) before the result. Every player sees it in the
   *  all-players total; no balance changes. Each click is a new request, so repeated clicks add up.
   *  The clicked button stays disabled for 1 second (and until its request returns); the others stay usable. */
  async addDummy(side: 'meron' | 'wala', amount: number): Promise<void> {
    const book = this.store.currentBook();
    if (!book || (book.state !== 'open' && book.state !== 'closed') || !this.store.submitter()) return;
    // Checked and set synchronously, so rapid clicks on the same button cannot send twice.
    const key = `${side}:${amount}`;
    if (this.dummyCooling().has(key)) return;
    this.setCooling(key, true);
    let elapsed = false;
    let done = false;
    const timer = setTimeout(() => {
      this.dummyTimers.delete(timer);
      elapsed = true;
      if (done) this.setCooling(key, false);
    }, DeclaratorDashboard.DUMMY_COOLDOWN_MS);
    this.dummyTimers.add(timer);
    this.dummyError.set(null);
    const value = amount.toFixed(2);
    try {
      await firstValueFrom(this.store.api.addDummyBet(book.match_id, { request_id: crypto.randomUUID(), side, amount: value }));
      const label = this.dummyAmounts.find(a => a.value === amount)?.label ?? value;
      this.message.set(`Dummy ${side.toUpperCase()} +${label} added. Every player sees it in the all-players total. No balance changed.`);
      this.store.reconcile();
    } catch (error) {
      this.dummyError.set(toApiError(error).message);
    } finally {
      done = true;
      if (elapsed) this.setCooling(key, false);
    }
  }

  /** Open (or reopen) or close simulated betting on the switch's fight. On an empty card, opening adds the
   *  next fight first. The API enforces every rule again. */
  async setBetting(open: boolean): Promise<void> {
    const control = this.store.betting();
    const eventId = this.store.monitor()?.event.id;
    if (!control || !this.store.submitter() || this.busy() || !(open ? control.can_open : control.can_close)) return;
    if (!control.match_id && !(open && eventId)) return;
    this.busy.set(true);
    this.actionError.set(null);
    try {
      const request = !open ? this.store.api.closeBetting(control.match_id!)
        : control.match_id ? this.store.api.openBetting(control.match_id) : this.store.api.openEventBetting(eventId!);
      const next = await firstValueFrom(request);
      this.message.set(`Fight #${next.match_number ?? control.match_number}: betting ${next.status === 'open' ? 'OPEN' : 'CLOSED'}.`);
      if (!control.match_id) { this.store.reconcile(); return; }
      this.store.applyBetting(next);
    } catch (error) { this.actionError.set(toApiError(error).message); this.store.reconcile(); }
    finally { this.busy.set(false); }
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

import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { firstValueFrom } from 'rxjs';
import { VideoStageComponent, WinningHistoryBoardComponent, derbyStats } from '@shared/arena';
import { CellDefDirective, Column, ConfirmService, DataTableComponent, EmptyStateComponent, ErrorStateComponent, StatusChipComponent } from '@shared/components';
import type { FightBookSummary, Match } from '@shared/models';
import { ArenaPointsPipe } from '@shared/pipes';
import { BettingControl, DECLARATION_LABELS, DeclarationOutcome, MatchRecord, WORKFLOW_LABELS, toApiError } from '@shared/services';
import { DeclarationHistoryDialog } from './history-dialog';
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

/** Why a fight was cancelled: the official record first, then the pending proposal. */
export function cancellationReason(record: MatchRecord): string | null {
  if (record.official_winner !== 'cancelled' && record.declaration?.outcome !== 'cancelled') return null;
  return record.match.cancel_reason || record.declaration?.reason || null;
}

@Component({
  selector: 'app-declarator-dashboard',
  imports: [DatePipe, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule, MatSelectModule,
    ArenaPointsPipe, VideoStageComponent, WinningHistoryBoardComponent, DataTableComponent, CellDefDirective, EmptyStateComponent, ErrorStateComponent, StatusChipComponent],
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
  readonly dummySide = signal<'meron' | 'wala'>('wala');
  readonly dummyAmount = signal('100');
  readonly actionError = signal<string | null>(null);
  readonly message = signal('');
  readonly labels = DECLARATION_LABELS;
  readonly statuses = WORKFLOW_LABELS;
  readonly resultLabel = (value: DeclarationOutcome) => this.labels[value];
  readonly multiplier = (value: string) => String(Number(value));
  hasDummy(b: FightBookSummary): boolean {
    return Number(b.dummy_meron ?? 0) > 0 || Number(b.dummy_wala ?? 0) > 0;
  }
  typeDummy(event: Event): void {
    const input = event.target as HTMLInputElement;
    const clean = input.value.replace(/[^\d.]/g, '').replace(/(\..*)\./g, '$1');
    input.value = clean;
    this.dummyAmount.set(clean);
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

  /** Add a dummy MERON or WALA amount to the open fight. The pool and odds move; no balance does. */
  async addDummy(event?: Event): Promise<void> {
    // This is a plain form, so stop the browser from reloading the page and dropping the request.
    event?.preventDefault();
    const book = this.store.currentBook();
    const amount = this.dummyAmount();
    if (!book || book.state !== 'open' || !book.accepting || !this.store.submitter() || this.busy()) return;
    if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0) {
      this.actionError.set('Enter a dummy amount above zero.');
      return;
    }
    this.busy.set(true);
    this.actionError.set(null);
    const side = this.dummySide();
    const key = `${book.match_id}:dummy:${side}:${amount}`;
    const request = this.requestKeys.get(key) ?? crypto.randomUUID();
    this.requestKeys.set(key, request);
    try {
      await firstValueFrom(this.store.api.addDummyBet(book.match_id, { request_id: request, side, amount: Number(amount).toFixed(2) }));
      this.requestKeys.delete(key);
      this.message.set(`Dummy ${side.toUpperCase()} bet added. It is in the all-player total and the odds, and no player's balance changed.`);
      this.store.reconcile();
    } catch (error) {
      this.actionError.set(toApiError(error).message);
    } finally {
      this.busy.set(false);
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

  /** "Next fight" line: the scheduled fight after the switch's fight, or when it will be prepared. */
  nextFight(control: BettingControl | null): string {
    if (control?.next_match_number) return `Fight #${control.next_match_number}`;
    if (control?.match_number) return `Fight #${control.match_number + 1} is prepared when Fight #${control.match_number}'s result is declared`;
    return 'Prepared when the current result is declared';
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

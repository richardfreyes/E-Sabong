import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { firstValueFrom } from 'rxjs';
import { VideoStageComponent, WinningHistoryBoardComponent, derbyStats } from '@shared/arena';
import { CellDefDirective, Column, ConfirmService, DataTableComponent, EmptyStateComponent, ErrorStateComponent, StatusChipComponent } from '@shared/components';
import type { Match } from '@shared/models';
import { DECLARATION_LABELS, DeclarationOutcome, MatchRecord, WORKFLOW_LABELS, toApiError } from '@shared/services';
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
  imports: [DatePipe, FormsModule, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule, MatSelectModule,
    VideoStageComponent, WinningHistoryBoardComponent, DataTableComponent, CellDefDirective, EmptyStateComponent, ErrorStateComponent, StatusChipComponent],
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
  readonly actionError = signal<string | null>(null);
  readonly message = signal('');
  readonly labels = DECLARATION_LABELS;
  readonly statuses = WORKFLOW_LABELS;
  readonly resultLabel = (value: DeclarationOutcome) => this.labels[value];
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
    { key: 'status', label: 'Verification' },
    { key: 'declared_by', label: 'Declared by', cell: r => r.declared_by ?? '—', hideOnMobile: true },
    { key: 'declared_at', label: 'Declared at', hideOnMobile: true },
    { key: 'approved_by', label: 'Approved by', cell: r => r.approved_by ?? '—', hideOnMobile: true },
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
        message: `${confirmQuestion(outcome, record.match.match_number)} It will be published after independent approval.`,
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
      this.message.set(`Fight #${record.match.match_number}: ${declaration.status === 'pending_approval' ? 'declaration submitted. Awaiting approval.' : this.statuses[declaration.status] + '.'}`);
      this.store.refresh();
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

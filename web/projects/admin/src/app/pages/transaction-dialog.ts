import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';

import type { Party, PointTransaction, TransactionType } from '@shared/models';
import { PointsPipe } from '@shared/pipes';

export const MANILA = '+0800';

export const TYPE_LABELS: Record<TransactionType, string> = {
  issue: 'Issue', credit: 'Credit', transfer: 'Transfer', refund: 'Refund', adjustment: 'Adjustment',
};

/** "Adjustment (debit)", "Issue", … */
export function typeLabel(tx: Pick<PointTransaction, 'type' | 'direction'>): string {
  return TYPE_LABELS[tx.type] + (tx.direction ? ` (${tx.direction})` : '');
}

export function partyLabel(party: Party): string {
  return party.kind === 'issuance' ? 'Points issuance' : party.username ?? party.account_id;
}

export interface TransactionDialogData {
  tx: PointTransaction;
  canRefund: boolean;
}

/** Read-only receipt of one committed ledger transaction. Closes with 'refund' when asked to reverse it. */
@Component({
  selector: 'app-transaction-dialog',
  imports: [MatDialogModule, MatButtonModule, MatIconModule, DatePipe, PointsPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    dl { display:grid; grid-template-columns:minmax(8rem,auto) minmax(0,1fr); gap:8px 16px; margin:0; font-size:.85rem; }
    dt { color:var(--text-secondary); }
    dd { margin:0; overflow-wrap:anywhere; font-variant-numeric:tabular-nums; }
    .parties { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin:16px 0; }
    .party { border:1px solid var(--border); border-radius:var(--radius-control); padding:12px; min-width:0; font-size:.8rem; color:var(--text-secondary); }
    .party strong { display:block; color:var(--text-primary); font-size:.9rem; margin:2px 0 6px; overflow-wrap:anywhere; }
    .party b { color:var(--text-primary); font-variant-numeric:tabular-nums; }
    .status { display:inline-flex; align-items:center; gap:4px; }
    .status mat-icon { font-size:1rem; width:1rem; height:1rem; }
    @media(max-width:479px) { .parties { grid-template-columns:minmax(0,1fr); } dl { grid-template-columns:minmax(0,1fr); } dt { margin-top:6px; } }
  `,
  template: `
    <h2 mat-dialog-title>{{ label }} · {{ tx.amount | points }} points</h2>
    <mat-dialog-content>
      <div class="parties">
        <section class="party" aria-label="Sender">Sender<strong>{{ party(tx.source) }}</strong>
          Balance <b>{{ tx.source_balance_before | points }}</b> → <b>{{ tx.source_balance_after | points }}</b></section>
        <section class="party" aria-label="Recipient">Recipient<strong>{{ party(tx.destination) }}</strong>
          Balance <b>{{ tx.destination_balance_before | points }}</b> → <b>{{ tx.destination_balance_after | points }}</b></section>
      </div>
      <dl>
        <dt>Reference</dt><dd data-field="reference">{{ tx.reference }}</dd>
        <dt>Status</dt><dd><span class="status"><mat-icon aria-hidden="true">{{ tx.status === 'reversed' ? 'undo' : 'check_circle' }}</mat-icon>{{ tx.status === 'reversed' ? 'Completed, reversed by refund' : 'Completed' }}</span></dd>
        <dt>Date and time</dt><dd>{{ tx.created_at | date: 'MMM d, y, h:mm:ss a' : MANILA }} (Asia/Manila)</dd>
        <dt>Initiated by</dt><dd>{{ tx.created_by_username ?? tx.created_by_id }}</dd>
        <dt>Reason / remarks</dt><dd>{{ tx.memo || '—' }}</dd>
        <dt>Sender wallet</dt><dd>{{ tx.source.account_id }}</dd>
        <dt>Recipient wallet</dt><dd>{{ tx.destination.account_id }}</dd>
        @if (tx.reverses_transaction_id) { <dt>Reverses</dt><dd>{{ tx.reverses_transaction_id }}</dd> }
        @if (tx.refund_transaction_id) { <dt>Refunded by</dt><dd>{{ tx.refund_transaction_id }}</dd> }
        <dt>Tenant</dt><dd>{{ tx.tenant_id }}</dd>
      </dl>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      @if (refundable) { <button mat-button type="button" [mat-dialog-close]="'refund'"><mat-icon>undo</mat-icon>Refund transfer</button> }
      <button mat-flat-button type="button" mat-dialog-close cdkFocusInitial>Close</button>
    </mat-dialog-actions>
  `,
})
export class TransactionDialogComponent {
  private readonly data = inject<TransactionDialogData>(MAT_DIALOG_DATA);
  readonly tx = this.data.tx;
  readonly label = typeLabel(this.tx);
  readonly refundable = this.data.canRefund && this.tx.kind === 'transfer' && this.tx.status === 'completed';
  readonly MANILA = MANILA;
  readonly party = partyLabel;
}

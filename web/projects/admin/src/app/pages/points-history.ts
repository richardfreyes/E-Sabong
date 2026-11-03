import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, effect, inject, input, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import { CellDefDirective, Column, DataTableComponent } from '@shared/components';
import type { PointTransaction, PointsHistoryQuery, TransactionStatus, TransactionType } from '@shared/models';
import { PointsPipe } from '@shared/pipes';
import { AdminApi, PagedList, saveBlob, toApiError } from '@shared/services';
import { MANILA, TYPE_LABELS, TransactionDialogComponent, TransactionDialogData, partyLabel, typeLabel } from './transaction-dialog';
import { UserPickerComponent } from './user-picker';

type Filter = 'type' | 'status' | 'sender_id' | 'recipient_id' | 'date_from' | 'date_to';

/** Read-only legacy points archive: searchable, filterable, paginated history with details and CSV export. */
@Component({
  selector: 'app-points-history',
  imports: [ReactiveFormsModule, DatePipe, PointsPipe, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule,
    MatSelectModule, DataTableComponent, CellDefDirective, UserPickerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display:block; min-width:0; }
    .head { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; margin-bottom:12px; }
    h2 { font-size:1rem; margin:0; }
    .filters { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-bottom:12px; }
    .filters > * { min-width:0; }
    .wide { grid-column:span 2; }
    .reversed { color:var(--text-secondary); text-decoration:line-through; }
    .ref { font-family:var(--font-mono, monospace); font-size:.75rem; }
    .feedback { color:var(--error-text); font-size:.85rem; margin:0 0 8px; }
    @media(max-width:1023px) { .filters { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media(max-width:599px) { .filters { grid-template-columns:minmax(0,1fr); } .wide { grid-column:auto; } }
  `,
  template: `
    <div class="head">
      <h2 id="points-history-title">Transaction history</h2>
      <div>
        @if (hasFilters()) { <button mat-button type="button" (click)="clear()">Clear filters</button> }
        <button mat-stroked-button type="button" (click)="export()" [disabled]="exporting()" [attr.aria-busy]="exporting()"><mat-icon>download</mat-icon>Export CSV</button>
      </div>
    </div>
    <div class="filters" role="group" aria-label="History filters">
      <mat-form-field subscriptSizing="dynamic"><mat-label>Type</mat-label>
        <mat-select [value]="filters().type ?? ''" (selectionChange)="set('type', $event.value)">
          <mat-option value="">All types</mat-option>@for (t of types; track t) { <mat-option [value]="t">{{ typeLabels[t] }}</mat-option> }
        </mat-select></mat-form-field>
      <mat-form-field subscriptSizing="dynamic"><mat-label>Status</mat-label>
        <mat-select [value]="filters().status ?? ''" (selectionChange)="set('status', $event.value)">
          <mat-option value="">All statuses</mat-option><mat-option value="completed">Completed</mat-option><mat-option value="reversed">Reversed</mat-option>
        </mat-select></mat-form-field>
      <mat-form-field subscriptSizing="dynamic"><mat-label>From date</mat-label>
        <input matInput type="date" [value]="filters().date_from ?? ''" [max]="filters().date_to ?? null" (change)="set('date_from', $any($event.target).value)" /></mat-form-field>
      <mat-form-field subscriptSizing="dynamic"><mat-label>To date</mat-label>
        <input matInput type="date" [value]="filters().date_to ?? ''" [min]="filters().date_from ?? null" (change)="set('date_to', $any($event.target).value)" /></mat-form-field>
      <app-user-picker class="wide" label="Sender" [formControl]="sender" hint="Any wallet this account sent from" />
      <app-user-picker class="wide" label="Recipient" [formControl]="recipient" hint="Any wallet this account received into" />
    </div>
    @if (error()) { <p class="feedback" role="alert">{{ error() }}</p> }
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
      [loading]="list.loading()" [error]="list.error()" [rowClickable]="true" searchPlaceholder="Search username, email, account ID or reference"
      emptyTitle="No transactions match" emptyMessage="Archived issues, credits, transfers, refunds and adjustments appear here."
      (search)="onSearch($event)" (pageChange)="list.setPage($event)" (retry)="list.load()" (rowClick)="open($event)">
      <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'MMM d, y, h:mm a' : MANILA }}</ng-template>
      <ng-template appCell="reference" let-row><span class="ref" [title]="row.reference">{{ row.reference.slice(0, 8) }}</span></ng-template>
      <ng-template appCell="amount" let-row><b [class.reversed]="row.status === 'reversed'">{{ row.amount | points }}</b></ng-template>
      <ng-template appCell="status" let-row>{{ row.status === 'reversed' ? 'Reversed' : 'Completed' }}</ng-template>
    </app-data-table>
  `,
})
export class PointsHistoryComponent {
  /** Explicit refresh requested by the archive viewer. */
  readonly refresh = input(0);
  private readonly api = inject(AdminApi);
  private readonly dialog = inject(MatDialog);
  private readonly destroy = inject(DestroyRef);
  readonly MANILA = MANILA;
  readonly types = Object.keys(TYPE_LABELS) as TransactionType[];
  readonly typeLabels = TYPE_LABELS;
  readonly filters = signal<Partial<Record<Filter, string | null>>>({});
  readonly exporting = signal(false);
  readonly error = signal<string | null>(null);
  readonly sender = new FormControl<string | null>(null);
  readonly recipient = new FormControl<string | null>(null);
  readonly list = new PagedList<PointTransaction>(q => this.api.transactions(q));
  private search: string | null = null;
  readonly columns: Column<PointTransaction>[] = [
    { key: 'created_at', label: 'When (Manila)' },
    { key: 'type', label: 'Type', cell: tx => typeLabel(tx) },
    { key: 'from', label: 'Sender', cell: tx => partyLabel(tx.source) },
    { key: 'to', label: 'Recipient', cell: tx => partyLabel(tx.destination) },
    { key: 'amount', label: 'Points', align: 'end' },
    { key: 'status', label: 'Status', hideOnMobile: true },
    { key: 'memo', label: 'Remarks', hideOnMobile: true },
    { key: 'created_by_username', label: 'By', hideOnMobile: true },
    { key: 'reference', label: 'Reference', hideOnMobile: true },
  ];

  constructor() {
    effect(() => { this.refresh(); untracked(() => this.list.load()); });
    this.sender.valueChanges.pipe(takeUntilDestroyed(this.destroy)).subscribe(id => this.set('sender_id', id));
    this.recipient.valueChanges.pipe(takeUntilDestroyed(this.destroy)).subscribe(id => this.set('recipient_id', id));
  }

  onSearch(q: string): void { this.search = q || null; this.list.setSearch(q); }

  hasFilters(): boolean { return Object.values(this.filters()).some(Boolean); }

  set(name: Filter, value: string | null): void {
    const next = value || null;
    if ((this.filters()[name] ?? null) === next) return;
    this.filters.update(f => ({ ...f, [name]: next }));
    this.error.set(null);
    this.list.setFilter(name, next);
  }

  clear(): void {
    this.sender.setValue(null, { emitEvent: false });
    this.recipient.setValue(null, { emitEvent: false });
    for (const name of Object.keys(this.filters()) as Filter[]) this.list.setFilter(name, null);
    this.filters.set({});
  }

  query(): PointsHistoryQuery {
    return { q: this.search, ...this.filters() } as PointsHistoryQuery;
  }

  export(): void {
    if (this.exporting()) return;
    this.exporting.set(true);
    this.error.set(null);
    this.api.exportTransactions(this.query()).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: blob => { this.exporting.set(false); saveBlob(blob, `points-history-${new Date().toISOString().slice(0, 10)}.csv`); },
      error: async error => {
        this.exporting.set(false);
        // Blob responses carry the JSON error body as a Blob.
        const body = error?.error instanceof Blob ? await error.error.text().catch(() => '') : '';
        let message = toApiError(error).message;
        try { message = JSON.parse(body).error.message ?? message; } catch { /* keep the generic message */ }
        this.error.set(message);
      },
    });
  }

  open(tx: PointTransaction): void {
    // Read-only archive: the receipt has no actions.
    this.dialog.open<TransactionDialogComponent, TransactionDialogData>(TransactionDialogComponent, {
      data: { tx }, width: '640px', maxWidth: '96vw',
    });
  }
}

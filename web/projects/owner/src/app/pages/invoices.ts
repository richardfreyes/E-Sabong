import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule } from '@angular/material/dialog';

import { CellDefDirective, Column, ConfirmService, DataTableComponent, PageHeaderComponent, StatusChipComponent } from '@shared/components';
import type { Invoice } from '@shared/models';
import { MoneyPipe } from '@shared/pipes';
import { PagedList, PlatformApi, ToastService } from '@shared/services';

@Component({
  selector: 'app-invoice-dialog',
  imports: [MatDialogModule, MatButtonModule, MoneyPipe, DatePipe, StatusChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>Invoice {{ inv.number }}</h2>
    <mat-dialog-content>
      <div class="mb-3 flex flex-wrap items-center gap-2 text-sm"><app-status-chip [status]="inv.status" /><span>{{ inv.tenant_name }}</span>
        <span class="text-on-surface-variant">Issued {{ inv.issued_at | date: 'mediumDate' }} · due {{ inv.due_at | date: 'mediumDate' }}</span></div>
      <table class="w-full border-collapse text-sm">
        <thead><tr class="text-left text-on-surface-variant"><th class="py-1 font-medium">Description</th><th class="py-1 text-right font-medium">Amount</th></tr></thead>
        <tbody>
          @for (l of inv.lines; track l.id) {
            <tr class="border-t border-outline-variant"><td class="py-2">{{ l.description }}</td><td class="py-2 text-right tabular-nums">{{ l.amount_cents | money: inv.currency }}</td></tr>
          }
        </tbody>
        <tfoot class="border-t border-outline-variant">
          <tr><td class="pt-2 text-on-surface-variant">Subtotal</td><td class="pt-2 text-right tabular-nums">{{ inv.subtotal_cents | money: inv.currency }}</td></tr>
          <tr><td class="text-on-surface-variant">Tax</td><td class="text-right tabular-nums">{{ inv.tax_cents | money: inv.currency }}</td></tr>
          <tr class="font-semibold"><td class="pt-1">Total</td><td class="pt-1 text-right tabular-nums">{{ inv.total_cents | money: inv.currency }}</td></tr>
        </tfoot>
      </table>
      @if (inv.payment_reference) { <p class="m-0 mt-3 text-sm">Paid {{ inv.paid_at | date: 'medium' }} · ref {{ inv.payment_reference }}</p> }
      @if (inv.notes) { <p class="m-0 mt-2 whitespace-pre-line text-xs text-on-surface-variant">{{ inv.notes }}</p> }
    </mat-dialog-content>
    <mat-dialog-actions align="end"><button mat-button type="button" mat-dialog-close>Close</button></mat-dialog-actions>
  `,
})
export class InvoiceDialog {
  readonly inv = inject<Invoice>(MAT_DIALOG_DATA);
}

@Component({
  selector: 'app-owner-invoices',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, StatusChipComponent, MatButtonModule, MatButtonToggleModule, MoneyPipe, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Invoices" subtitle="Payments are recorded manually against a bank or processor reference." />
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                    [loading]="list.loading()" [error]="list.error()" [filters]="true" [rowClickable]="true" searchPlaceholder="Search number or tenant"
                    emptyTitle="No invoices" emptyIcon="receipt_long" (search)="list.setSearch($event)" (pageChange)="list.setPage($event)"
                    (retry)="list.load()" (rowClick)="view($event)">
      <mat-button-toggle-group filters [value]="filter()" (change)="setFilter($event.value)" aria-label="Filter invoices" hideSingleSelectionIndicator>
        <mat-button-toggle value="">All</mat-button-toggle><mat-button-toggle value="open">Open</mat-button-toggle>
        <mat-button-toggle value="overdue">Overdue</mat-button-toggle><mat-button-toggle value="paid">Paid</mat-button-toggle>
      </mat-button-toggle-group>
      <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
      <ng-template appCell="total_cents" let-row>{{ row.total_cents | money: row.currency }}</ng-template>
      <ng-template appCell="due_at" let-row>{{ row.due_at | date: 'mediumDate' }}</ng-template>
      <ng-template appCell="actions" let-row>
        @if (row.status === 'open') {
          <div class="flex justify-end gap-1" (click)="$event.stopPropagation()" (keydown.enter)="$event.stopPropagation()">
            <button mat-button type="button" (click)="voidIt(row)">Void</button>
            <button mat-flat-button type="button" (click)="pay(row)">Mark paid</button>
          </div>
        }
      </ng-template>
    </app-data-table>
  `,
})
export class InvoicesPage {
  private readonly api = inject(PlatformApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly dialog = inject(MatDialog);
  readonly filter = signal('');
  readonly list = new PagedList<Invoice>((q) => this.api.invoices(q));
  readonly columns: Column<Invoice>[] = [
    { key: 'number', label: 'Number' },
    { key: 'tenant_name', label: 'Tenant' },
    { key: 'status', label: 'Status' },
    { key: 'total_cents', label: 'Total', align: 'end' },
    { key: 'due_at', label: 'Due', hideOnMobile: true },
    { key: 'actions', label: '', align: 'end' },
  ];

  constructor() {
    this.list.load();
  }

  setFilter(value: string): void {
    this.filter.set(value);
    this.list.setFilter('overdue', value === 'overdue' || null);
    this.list.setFilter('status', value && value !== 'overdue' ? value : null);
  }

  view(inv: Invoice): void {
    this.dialog.open(InvoiceDialog, { data: inv, width: '560px', maxWidth: '95vw' });
  }

  async pay(inv: Invoice): Promise<void> {
    const ref = await this.confirm.ask({ title: `Record payment for ${inv.number}`, message: 'Enter the bank transfer or processor reference.',
      confirmText: 'Mark paid', inputLabel: 'Payment reference', inputRequired: true });
    if (ref === null) return;
    this.api.markPaid(inv.id, ref).subscribe({ next: () => (this.toast.success('Payment recorded'), this.list.load()), error: (e) => this.toast.error(e) });
  }

  async voidIt(inv: Invoice): Promise<void> {
    const reason = await this.confirm.ask({ title: `Void ${inv.number}`, message: 'Voided invoices no longer count towards past-due status.',
      confirmText: 'Void invoice', destructive: true, inputLabel: 'Reason', inputRequired: true });
    if (reason === null) return;
    this.api.voidInvoice(inv.id, reason).subscribe({ next: () => (this.toast.success('Invoice voided'), this.list.load()), error: (e) => this.toast.error(e) });
  }
}

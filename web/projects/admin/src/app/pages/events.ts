import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { Router } from '@angular/router';

import { CellDefDirective, Column, DataTableComponent, FieldErrorComponent, PageHeaderComponent, StatusChipComponent } from '@shared/components';
import { HasPermissionDirective } from '@shared/directives';
import type { EventItem } from '@shared/models';
import { AdminApi, PagedList, toApiError } from '@shared/services';

/** Converts between <input type="datetime-local"> values and ISO timestamps with offset. */
export function toLocalInput(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromLocalInput(value: string | null): string | null {
  return value ? new Date(value).toISOString() : null;
}

@Component({
  selector: 'app-event-dialog',
  imports: [ReactiveFormsModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatButtonModule, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ event ? 'Edit event' : 'New event' }}</h2>
    <mat-dialog-content>
      <form [formGroup]="form" id="event-form" (ngSubmit)="save()" class="grid grid-cols-1 gap-x-3 sm:grid-cols-2" novalidate>
        <mat-form-field class="sm:col-span-2"><mat-label>Name</mat-label><input matInput formControlName="name" />
          <mat-error><app-field-error [control]="form.controls.name" /></mat-error></mat-form-field>
        <mat-form-field class="sm:col-span-2"><mat-label>Venue</mat-label><input matInput formControlName="venue" /></mat-form-field>
        <mat-form-field><mat-label>Starts</mat-label><input matInput type="datetime-local" formControlName="starts_at" />
          <mat-error><app-field-error [control]="form.controls.starts_at" /></mat-error></mat-form-field>
        <mat-form-field><mat-label>Ends (optional)</mat-label><input matInput type="datetime-local" formControlName="ends_at" /></mat-form-field>
        <mat-form-field class="sm:col-span-2"><mat-label>Description</mat-label><textarea matInput rows="3" formControlName="description"></textarea></mat-form-field>
        @if (error()) { <p class="m-0 text-sm text-error sm:col-span-2" role="alert">{{ error() }}</p> }
      </form>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close>Cancel</button>
      <button mat-flat-button type="submit" form="event-form">{{ event ? 'Save' : 'Create draft' }}</button>
    </mat-dialog-actions>
  `,
})
export class EventDialog {
  readonly event = inject<EventItem | null>(MAT_DIALOG_DATA);
  private readonly api = inject(AdminApi);
  private readonly ref = inject(MatDialogRef<EventDialog>);
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).nonNullable.group({
    name: [this.event?.name ?? '', [Validators.required, Validators.minLength(2)]],
    venue: [this.event?.venue ?? ''],
    starts_at: [toLocalInput(this.event?.starts_at ?? null), Validators.required],
    ends_at: [toLocalInput(this.event?.ends_at ?? null)],
    description: [this.event?.description ?? ''],
  });

  save(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    const body = { name: v.name, venue: v.venue || null, description: v.description || null,
                   starts_at: fromLocalInput(v.starts_at), ends_at: fromLocalInput(v.ends_at) };
    const req = this.event ? this.api.updateEvent(this.event.id, body) : this.api.createEvent(body);
    req.subscribe({ next: (e) => this.ref.close(e), error: (e) => this.error.set(toApiError(e).message) });
  }
}

@Component({
  selector: 'app-admin-events',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, StatusChipComponent, MatButtonModule, MatButtonToggleModule, MatIconModule,
            HasPermissionDirective, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Events" subtitle="Draft, publish and run events; matches and official results are managed inside each event.">
      <button *appHasPermission="'events.manage'" mat-flat-button type="button" (click)="create()"><mat-icon>add</mat-icon>New event</button>
    </app-page-header>
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                    [loading]="list.loading()" [error]="list.error()" [filters]="true" [rowClickable]="true" searchPlaceholder="Search name or venue"
                    emptyTitle="No events" emptyIcon="event" (search)="list.setSearch($event)" (sortChange)="list.setSort($event)"
                    (pageChange)="list.setPage($event)" (retry)="list.load()" (rowClick)="open($event)">
      <mat-button-toggle-group filters [value]="status()" (change)="setStatus($event.value)" aria-label="Filter by status" hideSingleSelectionIndicator>
        <mat-button-toggle value="">All</mat-button-toggle><mat-button-toggle value="draft">Draft</mat-button-toggle>
        <mat-button-toggle value="scheduled">Scheduled</mat-button-toggle><mat-button-toggle value="live">Live</mat-button-toggle>
        <mat-button-toggle value="completed">Completed</mat-button-toggle>
      </mat-button-toggle-group>
      <ng-template appCell="name" let-row><div class="font-medium">{{ row.name }}</div><div class="text-xs text-on-surface-variant">{{ row.venue }}</div></ng-template>
      <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
      <ng-template appCell="starts_at" let-row>{{ row.starts_at | date: 'medium' }}</ng-template>
      <ng-template appCell="progress" let-row>{{ row.completed_match_count }} / {{ row.match_count }}</ng-template>
    </app-data-table>
  `,
})
export class EventsPage {
  private readonly api = inject(AdminApi);
  private readonly dialog = inject(MatDialog);
  private readonly router = inject(Router);
  readonly status = signal('');
  readonly list = new PagedList<EventItem>((q) => this.api.events(q));
  readonly columns: Column<EventItem>[] = [
    { key: 'name', label: 'Event', sortable: true },
    { key: 'starts_at', label: 'Starts', sortable: true },
    { key: 'status', label: 'Status' },
    { key: 'progress', label: 'Matches done', align: 'end', hideOnMobile: true },
  ];

  constructor() {
    this.list.load();
  }

  setStatus(v: string): void {
    this.status.set(v);
    this.list.setFilter('status', v || null);
  }

  open(e: EventItem): void {
    void this.router.navigate(['/events', e.id]);
  }

  create(): void {
    this.dialog.open(EventDialog, { data: null, width: '600px', maxWidth: '95vw' }).afterClosed().subscribe((e?: EventItem) => {
      if (e) void this.router.navigate(['/events', e.id]);
    });
  }
}

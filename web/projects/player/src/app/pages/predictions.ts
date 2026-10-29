import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { Router } from '@angular/router';
import { debounceTime, merge } from 'rxjs';

import { CellDefDirective, type Column, DataTableComponent } from '@shared/components';
import type { Prediction } from '@shared/models';
import { PagedList, PortalApi, RealtimeService } from '@shared/services';
import { OUTCOME_TEXT } from '../core/match-phase';
import { PICK_LABEL, PICK_OUTCOME_LABEL, PICK_SIDES } from '../core/player-picks';
import { PageHeadComponent } from '../ui/page-head';

/** The player's own predictions across every device, newest first. Outcomes never move points. */
@Component({
  selector: 'app-player-predictions',
  imports: [DatePipe, MatFormFieldModule, MatSelectModule, DataTableComponent, CellDefDirective, PageHeadComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .filters { display: flex; flex-wrap: wrap; gap: 0.5rem; }
    .filters mat-form-field { flex: 1 1 9rem; max-width: 13rem; }
    .side { font-weight: 700; letter-spacing: 0.06em; }
    .side.meron { color: var(--meron-text); } .side.wala { color: var(--wala-text); } .side.draw { color: var(--success-text); }
    .outcome { font-weight: 700; letter-spacing: 0.08em; }
    .outcome.win { color: var(--success-text); } .outcome.lose { color: var(--error-text); }
    .outcome.void, .outcome.pending { color: var(--cancelled-text); font-weight: 500; letter-spacing: normal; }
    .note { margin: 0 0 1rem; color: var(--pp-ash); font-size: 0.85rem; }
  `,
  template: `
    <pp-page-head title="My predictions" subtitle="Your picks on every fight, on any device." />
    <p class="note">Predictions are for fun. Outcomes never add or remove demonstration points.</p>
    <app-data-table [columns]="columns" [rows]="list.rows()" [loading]="list.loading()" [error]="list.error()" [total]="list.total()"
                    [page]="list.page()" [pageSize]="list.pageSize()" [filters]="true" [rowClickable]="true"
                    searchPlaceholder="Search fight #, corner or event" emptyTitle="No predictions yet"
                    emptyMessage="Pick MERON, WALA or DRAW on a fight in the arena and it appears here."
                    (search)="list.setSearch($event)" (pageChange)="list.setPage($event)" (retry)="list.load()" (rowClick)="open($event)">
      <div filters class="filters">
        <mat-form-field subscriptSizing="dynamic"><mat-label>Outcome</mat-label>
          <mat-select [value]="list.filter('status') ?? ''" (selectionChange)="list.setFilter('status', $event.value || null)">
            <mat-option value="">All outcomes</mat-option><mat-option value="pending">Awaiting result</mat-option>
            @for (o of outcomes; track o) { <mat-option [value]="o">{{ outcomeLabel[o] }}</mat-option> }
          </mat-select></mat-form-field>
        <mat-form-field subscriptSizing="dynamic"><mat-label>Your pick</mat-label>
          <mat-select [value]="list.filter('side') ?? ''" (selectionChange)="list.setFilter('side', $event.value || null)">
            <mat-option value="">All picks</mat-option>
            @for (s of sides; track s) { <mat-option [value]="s">{{ sideLabel[s] }}</mat-option> }
          </mat-select></mat-form-field>
      </div>
      <ng-template appCell="side" let-row><span [class]="'side ' + row.side">{{ sideText(row) }}</span></ng-template>
      <ng-template appCell="status" let-row>
        <span [class]="'outcome ' + row.status">{{ statusText(row) }}</span>
      </ng-template>
      <ng-template appCell="created_at" let-row>{{ row.created_at | date: 'MMM d, h:mm a' }}</ng-template>
    </app-data-table>
  `,
})
export class PredictionsPage {
  private readonly api = inject(PortalApi);
  private readonly router = inject(Router);
  readonly list = new PagedList<Prediction>((q) => this.api.predictions(q));
  readonly sides = PICK_SIDES;
  readonly sideLabel = PICK_LABEL;
  readonly outcomes = ['win', 'lose', 'void'] as const;
  readonly outcomeLabel = PICK_OUTCOME_LABEL;
  readonly columns: Column<Prediction>[] = [
    { key: 'fight', label: 'Fight', cell: (p) => `#${p.match_number}` },
    { key: 'event', label: 'Event', cell: (p) => p.event_name, hideOnMobile: true },
    { key: 'corners', label: 'Corners', cell: (p) => `${p.meron_name} vs ${p.wala_name}`, hideOnMobile: true },
    { key: 'side', label: 'Your pick' },
    { key: 'official', label: 'Official result', cell: (p) => (p.match_status === 'cancelled' ? 'Cancelled' : p.official_outcome ? OUTCOME_TEXT[p.official_outcome] : '—') },
    { key: 'status', label: 'Outcome' },
    { key: 'created_at', label: 'Picked', hideOnMobile: true },
  ];

  constructor() {
    this.list.load();
    const realtime = inject(RealtimeService);
    // Picks made on another device and official results refresh the canonical list.
    merge(realtime.on('prediction.updated'), realtime.on('match.updated', 'match.result_declared', 'match.result_corrected'), realtime.on('connected'))
      .pipe(debounceTime(300), takeUntilDestroyed(inject(DestroyRef)))
      .subscribe(() => this.list.load());
  }

  readonly sideText = (p: Prediction) => PICK_LABEL[p.side];
  readonly statusText = (p: Prediction) => (p.status === 'pending' ? (p.open ? 'Open' : 'Awaiting result') : PICK_OUTCOME_LABEL[p.status]);

  open(p: Prediction): void {
    void this.router.navigate(['/matches', p.match_id]);
  }
}

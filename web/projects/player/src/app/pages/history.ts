import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { Router } from '@angular/router';

import { ArenaStore } from '../core/arena.store';
import { eventPhase } from '../core/match-phase';
import { DerbyViewComponent } from '../ui/derby-view';
import { PageHeadComponent } from '../ui/page-head';

/** Fight-by-fight history of one derby: record, result board and the full card. */
@Component({
  selector: 'app-player-history',
  imports: [DatePipe, MatFormFieldModule, MatSelectModule, PageHeadComponent, DerbyViewComponent],
  providers: [ArenaStore],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <pp-page-head title="Match history" subtitle="Every fight of a derby and how it was decided.">
      @if (store.events().length) {
        <mat-form-field subscriptSizing="dynamic" class="w-72 max-w-full">
          <mat-label>Event</mat-label>
          <mat-select [value]="store.eventId()" (selectionChange)="choose($event.value)">
            @for (e of store.events(); track e.id) {
              <mat-option [value]="e.id">{{ e.name }} ({{ phase(e).label }}, {{ e.starts_at | date: 'MMM d' }})</mat-option>
            }
          </mat-select>
        </mat-form-field>
      }
    </pp-page-head>
    @if (!store.loading() && !store.event()) {
      <p class="pp-panel p-6 pp-muted m-0">No published events yet. Results appear here once a derby gets under way.</p>
    } @else {
      <pp-derby-view />
    }
  `,
})
export class HistoryPage {
  /** `?event=<id>` from the arena's "Open match history" link. */
  readonly event = input<string | null>(null);
  readonly store = inject(ArenaStore);
  private readonly router = inject(Router);
  readonly phase = eventPhase;

  constructor() {
    queueMicrotask(() => this.store.init(this.event()));
  }

  choose(id: string): void {
    this.store.select(id);
    void this.router.navigate([], { queryParams: { event: id }, replaceUrl: true });
  }
}

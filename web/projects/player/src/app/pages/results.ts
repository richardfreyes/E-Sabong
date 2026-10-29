import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MatPaginatorModule } from '@angular/material/paginator';

import type { Match } from '@shared/models';
import { PagedList, PortalApi } from '@shared/services';
import { FightListComponent } from '../ui/fight-list';
import { PageHeadComponent } from '../ui/page-head';

@Component({
  selector: 'app-player-results',
  imports: [MatPaginatorModule, PageHeadComponent, FightListComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `.skeleton { height: 20rem; border-radius: 1.25rem; background: var(--pp-pit); animation: pp-pulse 1.6s ease-in-out infinite; }`,
  template: `
    <pp-page-head title="Results" subtitle="Official results across every event, newest first." />
    @if (list.error()) {
      <p class="pp-panel p-6 m-0" role="alert">Results didn't load: {{ list.error() }} <button type="button" class="underline" (click)="list.load()">Try again</button></p>
    } @else if (list.loading() && !list.rows().length) {
      <div class="skeleton" aria-label="Loading results"></div>
    } @else {
      <section class="pp-panel overflow-hidden">
        <pp-fight-list [matches]="list.rows()" [showEvent]="true" emptyText="No official results yet." />
      </section>
      @if (list.total() > list.pageSize()) {
        <mat-paginator class="mt-3" [length]="list.total()" [pageIndex]="list.page() - 1" [pageSize]="list.pageSize()" [pageSizeOptions]="[25, 50, 100]"
                       (page)="list.setPage({ page: $event.pageIndex + 1, pageSize: $event.pageSize })" aria-label="Select page" />
      }
    }
  `,
})
export class ResultsPage {
  private readonly api = inject(PortalApi);
  readonly list = new PagedList<Match>((q) => this.api.results(q));

  constructor() {
    this.list.load();
  }
}

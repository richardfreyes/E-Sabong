import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatPaginatorModule } from '@angular/material/paginator';
import { RouterLink } from '@angular/router';

import type { EventItem } from '@shared/models';
import { PagedList, PortalApi } from '@shared/services';
import { eventPhase } from '../core/match-phase';
import { PageHeadComponent } from '../ui/page-head';
import { PhasePillComponent } from '../ui/phase-pill';

@Component({
  selector: 'app-player-events',
  imports: [DatePipe, RouterLink, MatButtonToggleModule, MatFormFieldModule, MatInputModule, MatIconModule, MatPaginatorModule,
            PageHeadComponent, PhasePillComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr)); gap: 1rem; }
    .card { position: relative; display: flex; flex-direction: column; gap: 0.6rem; padding: 1.1rem; border-radius: 1.25rem; overflow: hidden;
      border: 1px solid var(--pp-rope); background: var(--pp-pit); color: inherit; text-decoration: none; min-height: 11rem; }
    .card:hover, .card:focus-visible { border-color: var(--control-border); background: var(--pp-pit-raised); }
    .card.live { border-color: color-mix(in srgb, var(--pp-lamp) 45%, transparent);
      background: radial-gradient(ellipse 80% 70% at 50% -20%, color-mix(in srgb, var(--pp-lamp) 14%, transparent), transparent 70%), var(--pp-pit); }
    .top { display: flex; align-items: flex-start; justify-content: space-between; gap: 0.75rem; }
    h2 { margin: 0; font-size: 1.7rem; color: var(--pp-chalk); overflow-wrap: anywhere; }
    .when { margin: 0; color: var(--pp-ash); font-size: 0.85rem; }
    .venue { display: flex; align-items: center; gap: 0.3rem; color: var(--pp-ash); font-size: 0.85rem; }
    .venue mat-icon { width: 16px; height: 16px; font-size: 16px; }
    .progress { margin-top: auto; display: flex; align-items: center; gap: 0.6rem; color: var(--pp-ash); font-size: 0.8rem; }
    .bar { flex: 1; height: 0.35rem; border-radius: 999px; background: var(--pp-rope); overflow: hidden; }
    .bar span { display: block; height: 100%; background: var(--pp-chalk); border-radius: 999px; }
    .skeleton { height: 11rem; border-radius: 1.25rem; background: var(--pp-pit); animation: pp-pulse 1.6s ease-in-out infinite; }
    .filters { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
  `,
  template: `
    <pp-page-head title="Events" subtitle="Live derbies first, then what's coming up and what has closed." />
    <div class="filters">
      <mat-button-toggle-group [value]="status()" (change)="setStatus($event.value)" aria-label="Filter events" hideSingleSelectionIndicator>
        <mat-button-toggle value="">All</mat-button-toggle><mat-button-toggle value="live">Live</mat-button-toggle>
        <mat-button-toggle value="scheduled">Upcoming</mat-button-toggle><mat-button-toggle value="completed">Closed</mat-button-toggle>
      </mat-button-toggle-group>
      <mat-form-field subscriptSizing="dynamic" class="sm:ml-auto sm:w-72 w-full">
        <mat-icon matPrefix aria-hidden="true">search</mat-icon>
        <input matInput type="search" placeholder="Search events or venues" aria-label="Search events or venues" (input)="search($any($event.target).value)" />
      </mat-form-field>
    </div>
    @if (list.error()) {
      <p class="pp-panel p-6 m-0" role="alert">Events didn't load: {{ list.error() }} <button type="button" class="underline" (click)="list.load()">Try again</button></p>
    } @else if (list.loading() && !list.rows().length) {
      <div class="grid">@for (i of [1, 2, 3]; track i) { <div class="skeleton"></div> }</div>
    } @else if (!list.rows().length) {
      <p class="pp-panel p-6 pp-muted m-0">No events match. Clear the search or pick another filter.</p>
    } @else {
      <div class="grid">
        @for (e of list.rows(); track e.id) {
          <a [routerLink]="['/events', e.id]" class="card" [class.live]="e.status === 'live'">
            <div class="top"><h2 class="pp-display">{{ e.name }}</h2><pp-phase-pill [phase]="phase(e)" /></div>
            <p class="when">{{ e.starts_at | date: 'EEEE d MMMM y, h:mm a' }}</p>
            @if (e.venue) { <span class="venue"><mat-icon aria-hidden="true">place</mat-icon>{{ e.venue }}</span> }
            <span class="progress">
              <span class="bar" aria-hidden="true"><span [style.width.%]="e.match_count ? (e.completed_match_count / e.match_count) * 100 : 0"></span></span>
              {{ e.completed_match_count }} of {{ e.match_count }} fights decided
            </span>
          </a>
        }
      </div>
      <mat-paginator class="mt-4" [length]="list.total()" [pageIndex]="list.page() - 1" [pageSize]="list.pageSize()" [pageSizeOptions]="[12, 24, 48]"
                     (page)="list.setPage({ page: $event.pageIndex + 1, pageSize: $event.pageSize })" aria-label="Select page" />
    }
  `,
})
export class EventsPage {
  private readonly api = inject(PortalApi);
  readonly status = signal('');
  readonly phase = eventPhase;
  readonly list = new PagedList<EventItem>((q) => this.api.events(q));
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    this.list.pageSize.set(12);
    this.list.load();
  }

  setStatus(v: string): void {
    this.status.set(v);
    this.list.setFilter('status', v || null);
  }

  search(q: string): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.list.setSearch(q.trim()), 300);
  }
}

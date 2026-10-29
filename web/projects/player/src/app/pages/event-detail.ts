import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, effect, inject, input, untracked } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { RouterLink } from '@angular/router';

import { ArenaStore } from '../core/arena.store';
import { eventPhase } from '../core/match-phase';
import { DerbyViewComponent } from '../ui/derby-view';
import { PageHeadComponent } from '../ui/page-head';
import { PhasePillComponent } from '../ui/phase-pill';

@Component({
  selector: 'app-player-event',
  imports: [DatePipe, RouterLink, MatIconModule, PageHeadComponent, PhasePillComponent, DerbyViewComponent],
  providers: [ArenaStore],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .back { display: inline-flex; align-items: center; gap: 0.25rem; margin-bottom: 0.4rem; color: var(--pp-ash); text-decoration: none; font-size: 0.85rem; }
    .back:hover { color: var(--pp-chalk); }
    .back mat-icon { width: 18px; height: 18px; font-size: 18px; }
    .about { margin: -0.5rem 0 1.25rem; max-width: 70ch; color: var(--pp-ash); white-space: pre-line; font-size: 0.9rem; }
  `,
  template: `
    @let e = store.event();
    <pp-page-head [title]="e?.name ?? 'Event'" [subtitle]="e ? (e.starts_at | date: 'EEEE d MMMM y, h:mm a') + (e.venue ? ', ' + e.venue : '') : null">
      <a back routerLink="/events" class="back"><mat-icon aria-hidden="true">arrow_back</mat-icon>Events</a>
      @if (e) { <pp-phase-pill [phase]="phase(e)" /> }
    </pp-page-head>
    @if (e?.description) { <p class="about">{{ e!.description }}</p> }
    @if (store.error()) {
      <p class="pp-panel p-6 m-0" role="alert">This event isn't available: {{ store.error() }}</p>
    } @else {
      <pp-derby-view />
    }
  `,
})
export class EventDetailPage {
  readonly id = input.required<string>();
  readonly store = inject(ArenaStore);
  readonly phase = eventPhase;

  constructor() {
    effect(() => {
      const id = this.id();
      untracked(() => this.store.init(id));
    });
  }
}

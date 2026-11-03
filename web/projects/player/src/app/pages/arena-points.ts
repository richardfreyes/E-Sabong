import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, effect, inject, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatPaginatorModule } from '@angular/material/paginator';

import type { PlayEntryKind, PlayWalletEntry } from '@shared/models';
import { ArenaPointsPipe, toCents } from '@shared/pipes';
import { PagedList, PortalApi, RealtimeService } from '@shared/services';
import { PlayWalletStore } from '../core/play-betting';
import { PageHeadComponent } from '../ui/page-head';

const KIND_LABEL: Record<PlayEntryKind, string> = {
  grant: 'Starting Arena Points', stake: 'Bet placed', payout: 'Win credited', refund: 'Refund', resettlement: 'Correction',
};

/**
 * The player's Arena Points: the same balance as the header and the betting panel (PlayWalletStore)
 * and their own ledger from the API, newest first. Read-only; nothing here changes a balance.
 */
@Component({
  selector: 'app-player-arena-points',
  imports: [DatePipe, MatButtonModule, MatIconModule, MatPaginatorModule, ArenaPointsPipe, PageHeadComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .hero { display: grid; grid-template-columns: auto 1fr; gap: 1.5rem; align-items: center; padding: 1.4rem 1.5rem; margin-bottom: 1rem;
      border-radius: 1.25rem; border: 1px solid color-mix(in srgb, var(--mat-sys-primary) 35%, var(--pp-rope));
      background: radial-gradient(ellipse 60% 120% at 0% 0%, color-mix(in srgb, var(--mat-sys-primary) 16%, transparent), transparent 70%), var(--pp-pit); }
    .label { margin: 0; color: var(--pp-ash); font-size: 0.85rem; }
    .hero > div { min-width: 0; }
    .balance { overflow-wrap: anywhere; margin: 0.4rem 0 0; font-size: clamp(3rem, 2.4rem + 3vw, 4.75rem); color: var(--pp-chalk); }
    .note { display: flex; gap: 0.6rem; margin: 0; color: var(--pp-ash); font-size: 0.875rem; max-width: 46ch; justify-self: end; }
    .note mat-icon { flex: none; color: var(--mat-sys-primary); }
    ul { list-style: none; margin: 0; padding: 0; }
    li { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, auto); gap: 0.25rem 1rem; padding: 0.75rem 1.1rem; }
    li + li { border-top: 1px solid var(--pp-rope); }
    .what { color: var(--pp-chalk); font-weight: 500; }
    .meta { color: var(--pp-ash); font-size: 0.8rem; }
    .delta { overflow-wrap: anywhere; font-size: 1.4rem; text-align: right; color: var(--pp-chalk); }
    .delta.out { color: var(--pp-ash); }
    .after { overflow-wrap: anywhere; text-align: right; color: var(--pp-ash); font-size: 0.8rem; font-variant-numeric: tabular-nums; }
    .empty { padding: 1.25rem 1.1rem; color: var(--pp-ash); }
    .delta.in { color: var(--mat-sys-primary); }
    .error { padding: 1.25rem 1.1rem; color: var(--mat-sys-error); display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }
    .skeleton { height: 3.5rem; margin: 0.75rem 1.1rem; border-radius: 0.6rem; background: var(--pp-pit-raised); animation: pp-pulse 1.6s ease-in-out infinite; }
    @media (max-width: 767px) { .hero { grid-template-columns: 1fr; gap: 0.75rem; } .note { justify-self: start; } }
  `,
  template: `
    <pp-page-head title="Arena Points history" />
    <section class="hero" aria-label="Arena Points balance">
      <div>
        <p class="label">Available Arena Points</p>
        <p class="balance pp-num" data-field="balance">{{ wallet.balance() | arenaPoints }}</p>
      </div>
      <p class="note"><mat-icon aria-hidden="true">info</mat-icon>Arena Points are for simulated play only. They have no cash value and
        cannot be bought, transferred, withdrawn or redeemed.</p>
    </section>
    <section class="pp-panel overflow-hidden" aria-labelledby="statement">
      <div class="pp-panel-head"><h2 id="statement" class="pp-h2">Statement</h2></div>
      @if (list.error(); as e) {
        <p class="error" role="alert">{{ e }} <button mat-stroked-button type="button" (click)="list.load()">Try again</button></p>
      } @else if (list.loading() && !list.rows().length) {
        @for (i of [1, 2, 3]; track i) { <div class="skeleton"></div> }
      } @else {
        <ul>
          @for (e of list.rows(); track e.id) {
            <li>
              <span class="what">{{ kindLabel[e.kind] ?? e.kind }}@if (e.match_number) { · Fight #{{ e.match_number }}@if (e.event_name) {, {{ e.event_name }}}}</span>
              <span class="pp-num delta" [class.in]="isCredit(e)" [class.out]="!isCredit(e)">{{ signed(e.delta) }}</span>
              <span class="meta">{{ e.created_at | date: 'MMM d, y, h:mm a' : '+0800' }}</span>
              <span class="after">Balance {{ e.balance_after | arenaPoints }}</span>
            </li>
          } @empty {
            <li class="empty">No Arena Points activity yet. Your starting Arena Points, bets and wins appear here.</li>
          }
        </ul>
      }
    </section>
    @if (list.total() > list.pageSize()) {
      <mat-paginator class="mt-3" [length]="list.total()" [pageIndex]="list.page() - 1" [pageSize]="list.pageSize()"
                     (page)="list.setPage({ page: $event.pageIndex + 1, pageSize: $event.pageSize })" aria-label="Select page" />
    }
  `,
})
export class ArenaPointsPage {
  private readonly api = inject(PortalApi);
  readonly wallet = inject(PlayWalletStore);
  readonly kindLabel = KIND_LABEL;
  readonly list = new PagedList<PlayWalletEntry>((q) => this.api.arenaPointsHistory(q));

  constructor() {
    this.wallet.load();
    effect(() => { this.wallet.balance(); untracked(() => this.list.load()); });
    // A balance change anywhere (bet, settlement, refund) adds a ledger row: reload the statement.
    inject(RealtimeService).on('wallet.updated', 'connected').pipe(takeUntilDestroyed(inject(DestroyRef))).subscribe(() => this.list.load());
  }

  isCredit(e: PlayWalletEntry): boolean {
    return (toCents(e.delta) ?? 0) > 0;
  }

  /** "+500.00" / "−100.00" from the server's decimal delta. */
  signed(delta: string): string {
    const cents = toCents(delta);
    if (cents === null) return '—';
    const text = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(cents) / 100);
    return cents > 0 ? `+${text}` : cents < 0 ? `−${text}` : text;
  }
}

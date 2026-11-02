import { DestroyRef, Injectable, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subscription, firstValueFrom } from 'rxjs';

import type { BetPosition, BetSide, BettingConfig, BetLimits, FightBook, PlayWallet, PlayerBook, TenantProfile } from '@shared/models';
import { formatCredits, toCents } from '@shared/pipes';
import { AuthStore, PortalApi, RealtimeService, toApiError } from '@shared/services';

export const BET_SIDES: readonly BetSide[] = ['meron', 'wala', 'draw'];
export const BET_LABEL: Record<BetSide, string> = { meron: 'MERON', wala: 'WALA', draw: 'DRAW' };
/** Quick-select chips, in whole units of the platform currency. */
export const BET_CHIPS = [50, 100, 500, 1000, 5000, 10000] as const;

/**
 * Projected gross return of a MERON or WALA stake at the live, provisional odds, rounded half up
 * to the cent like the server's `estimated_pays`. Display only: payouts are computed by the server
 * at settlement, and every API response replaces these estimates with the server's own figures.
 */
export function estimatePays(stake: string, odds: string | null): string | null {
  const s = Number(stake);
  if (!(s > 0)) return '0.00';
  if (odds === null) return null;
  return (Math.round(s * Number(odds) * 100 + 1e-6) / 100).toFixed(2);
}

/** Re-estimate the open MERON/WALA positions against newer odds; settled and DRAW figures stay. */
export function reprice(mine: BetPosition, book: FightBook): BetPosition {
  const side = (s: 'meron' | 'wala') => (mine[s].result ? mine[s] : { ...mine[s], pays: estimatePays(mine[s].stake, book.odds[s]) });
  return { ...mine, meron: side('meron'), wala: side('wala') };
}

/**
 * The signed-in player's play balance and the tenant's betting switch. Server-owned: it only
 * ever takes values from the API or from `wallet.updated` frames newer than what it holds.
 */
@Injectable({ providedIn: 'root' })
export class PlayWalletStore {
  private readonly api = inject(PortalApi);
  private version = -1;

  readonly config = signal<BettingConfig | null>(null);
  readonly enabled = computed(() => !!this.config()?.enabled);
  readonly balance = signal<string | null>(null);
  /** Bumped when the balance changes, for a brief highlight. */
  readonly pulse = signal(0);

  constructor() {
    const realtime = inject(RealtimeService);
    realtime.on<{ balance: string; version: number }>('wallet.updated').subscribe((w) => this.apply(w));
    // An administrator switched betting on or off: refetch, which also creates the wallet on first use.
    realtime.on('betting.config_updated').subscribe(() => this.load());
    // A reconnect may have missed frames: resynchronise.
    realtime.on('connected').subscribe(() => this.config() && this.load());
    const auth = inject<AuthStore<TenantProfile>>(AuthStore);
    let owner: string | null | undefined;
    effect(() => {
      const id = auth.user()?.id ?? null;
      if (owner !== undefined && id !== owner) this.reset();
      owner = id;
    });
  }

  load(): void {
    this.api.betting().subscribe({
      next: (c) => {
        this.config.set(c);
        if (c.wallet) this.apply(c.wallet);
      },
      error: () => undefined,
    });
  }

  apply(wallet: Pick<PlayWallet, 'balance' | 'version'>): void {
    if (wallet.version < this.version) return;
    const changed = wallet.balance !== this.balance();
    this.version = wallet.version;
    this.balance.set(wallet.balance);
    if (changed) this.pulse.update((n) => n + 1);
  }

  private reset(): void {
    this.version = -1;
    this.balance.set(null);
    this.config.set(null);
  }
}

/**
 * Live book of the fight on stage: all-player pools and odds from `book.updated` broadcasts,
 * plus the player's own stakes and projected returns from the API. Frames carry a version, so
 * repeated or out-of-order ones are dropped; a reconnect refetches everything.
 */
@Injectable()
export class FightBookStore {
  private readonly destroy = inject(DestroyRef);
  private request?: Subscription;
  private readonly api = inject(PortalApi);
  private readonly wallet = inject(PlayWalletStore);
  private ticket = 0;

  readonly matchId = signal<string | null>(null);
  readonly book = signal<FightBook | null>(null);
  readonly mine = signal<BetPosition | null>(null);
  readonly limits = signal<BetLimits | null>(null);
  readonly loading = signal(false);
  /** The side whose bet is being submitted. */
  readonly placing = signal<BetSide | null>(null);
  readonly error = signal<string | null>(null);
  readonly notice = signal<string | null>(null);

  constructor() {
    const realtime = inject(RealtimeService);
    const destroy = inject(DestroyRef);
    realtime.on<FightBook>('book.updated').pipe(takeUntilDestroyed(destroy)).subscribe((b) => this.onBook(b));
    realtime
      .on<{ match_id: string }>('bets.updated', 'bets.settled')
      .pipe(takeUntilDestroyed(destroy))
      .subscribe((m) => m.match_id === this.matchId() && this.reload());
    realtime.on('connected').pipe(takeUntilDestroyed(destroy)).subscribe(() => this.reload());
  }

  watch(matchId: string | null): void {
    if (matchId === this.matchId()) return;
    this.request?.unsubscribe();
    this.ticket++;
    this.matchId.set(matchId);
    this.book.set(null);
    this.mine.set(null);
    this.limits.set(null);
    this.error.set(null);
    this.notice.set(null);
    this.loading.set(false);
    this.reload();
  }

  reload(): void {
    const id = this.matchId();
    if (!id) return;
    const ticket = ++this.ticket;
    this.request?.unsubscribe();
    this.loading.set(!this.book());
    this.request = this.api.book(id).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: (view) => ticket === this.ticket && this.applyView(view),
      error: (e) => {
        if (ticket !== this.ticket) return;
        this.loading.set(false);
        this.error.set(toApiError(e).message);
      },
    });
  }

  async place(side: BetSide, amount: string): Promise<boolean> {
    const id = this.matchId();
    if (!id || this.placing()) return false;
    this.placing.set(side);
    this.error.set(null);
    this.notice.set(null);
    // One key per click: a resend after a lost response replays the same bet, never a second one.
    const requestId = crypto.randomUUID();
    try {
      let placed;
      try {
        placed = await firstValueFrom(this.api.placeBet(id, side, amount, requestId));
      } catch (err) {
        if (toApiError(err).code !== 'network_error') throw err;
        placed = await firstValueFrom(this.api.placeBet(id, side, amount, requestId));
      }
      if (id === this.matchId()) this.applyView(placed);
      this.notice.set(`Bet accepted · ${BET_LABEL[side]} ${formatCredits(toCents(placed.bet.amount) ?? 0, true)}`);
      return true;
    } catch (err) {
      this.error.set(toApiError(err).message);
      this.reload();
      return false;
    } finally {
      this.placing.set(null);
    }
  }

  private applyView(view: PlayerBook): void {
    if (view.book.match_id !== this.matchId()) return;
    const current = this.book();
    // A frame newer than this response may already be applied; keep its totals and odds.
    const book = current && current.version > view.book.version ? current : view.book;
    this.book.set(book);
    this.mine.set(book === view.book ? view.mine : reprice(view.mine, book));
    this.limits.set(view.limits);
    this.wallet.apply(view.wallet);
    this.loading.set(false);
  }

  private onBook(frame: FightBook): void {
    if (frame.match_id !== this.matchId()) return;
    const current = this.book();
    if (current && frame.version <= current.version) return;
    const { event_id: _event, ...book } = frame as FightBook & { event_id?: string };
    this.book.set(book);
    // "Bet accepted" belongs to the open book; it is stale once betting closes or the fight settles.
    if (current && book.state !== current.state) this.notice.set(null);
    const mine = this.mine();
    if (mine) this.mine.set(reprice(mine, book));
    // Settlement changes results; the personal figures come from the server.
    if (book.state === 'settled' && current?.state !== 'settled') this.reload();
  }
}

import { DestroyRef, Injectable, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subscription, auditTime, firstValueFrom, map, of, retry, switchMap, tap, timeout, timer } from 'rxjs';

import type { BetPosition, BetSide, BettingConfig, BetLimits, FightBook, PlayWallet, PlayerBook, TenantProfile } from '@shared/models';
import { formatArenaPoints, toCents } from '@shared/pipes';
import { AuthStore, PortalApi, RealtimeService, toApiError } from '@shared/services';

export const BET_SIDES: readonly BetSide[] = ['meron', 'wala', 'draw'];
export const BET_LABEL: Record<BetSide, string> = { meron: 'MERON', wala: 'WALA', draw: 'DRAW' };
/** Quick-select chips, in whole balance units. */
export const BET_CHIPS = [50, 100, 500, 1000, 5000, 10000] as const;

/**
 * Projected gross return of a new MERON or WALA stake at the live odds, rounded half up to the cent
 * like the server's `estimated_pays`. MERON/WALA are paid the 2-decimal board price at close.
 */
export function estimatePays(stake: string, odds: string | null): string | null {
  const s = Number(stake);
  if (!(s > 0)) return '0.00';
  if (odds === null) return null;
  return (Math.round(s * Number(odds) * 100 + 1e-6) / 100).toFixed(2);
}

/** Payout and net profit of the amount being typed, at the live odds. `1.83` on `1000` is `1830` and `830`. */
export function ticketEstimate(amount: string, odds: string | null): { payout: string; profit: string } | null {
  const payout = estimatePays(amount, odds);
  const stake = Number(amount);
  if (payout === null || !(stake > 0)) return null;
  return { payout, profit: (Math.round((Number(payout) - stake) * 100) / 100).toFixed(2) };
}

/** The position as the server sent it. The panel prices open MERON/WALA stakes at the live board odds. */
export function reprice(mine: BetPosition, _book: FightBook): BetPosition {
  return mine;
}

/** Live video needs the available balance plus open (unsettled) fight stakes to reach this, in cents. */
export const MIN_STREAM_CENTS = 10000;
/** How long one wallet request may take, and how often it is retried before access shows an error. */
const LOAD_TIMEOUT_MS = 10000;
const LOAD_RETRIES = 2;

/**
 * The signed-in player's play balance, open stakes and the tenant's betting switch. Server-owned:
 * it only ever takes values from the API or from `wallet.updated` frames newer than what it holds.
 */
@Injectable({ providedIn: 'root' })
export class PlayWalletStore {
  private readonly api = inject(PortalApi);
  private version = -1;

  readonly config = signal<BettingConfig | null>(null);
  readonly enabled = computed(() => !!this.config()?.enabled);
  readonly balance = signal<string | null>(null);
  /** Unsettled fight stakes. They left the balance when accepted, so the two never overlap. */
  readonly openStakes = signal<string | null>(null);
  /** The wallet could not be read after retries; null while loading or once it has loaded. */
  readonly loadError = signal<string | null>(null);
  /** Bumped when the balance changes, for a brief highlight. */
  readonly pulse = signal(0);

  constructor() {
    const realtime = inject(RealtimeService);
    realtime.on<Pick<PlayWallet, 'balance' | 'version' | 'open_stakes'>>('wallet.updated').subscribe((w) => this.apply(w));
    // An administrator switched betting on or off: refetch, which also creates the wallet on first use.
    realtime.on('betting.config_updated').subscribe(() => this.load());
    // A reconnect may have missed frames (or follow a failed first read): resynchronise.
    realtime.on('connected').subscribe(() => (this.config() || this.loadError()) && this.load());
    const auth = inject<AuthStore<TenantProfile>>(AuthStore);
    let owner: string | null | undefined;
    effect(() => {
      const id = auth.user()?.id ?? null;
      if (owner !== undefined && id !== owner) this.reset();
      owner = id;
    });
  }

  private request?: Subscription;

  load(): void {
    this.request?.unsubscribe();
    this.loadError.set(null);
    // A hung or failed request must never leave live video "checking" forever: time out, retry
    // briefly, then report the failure so the player can try again.
    this.request = this.api.betting().pipe(timeout(LOAD_TIMEOUT_MS), tap(c => this.config.set(c)), switchMap(c => c.wallet ? of(c)
      : this.api.streamBalance().pipe(timeout(LOAD_TIMEOUT_MS), map(wallet => ({ ...c, wallet })))),
      retry({ count: LOAD_RETRIES, delay: (_e, attempt) => timer(1000 * attempt) })).subscribe({
      next: (c) => {
        if (c.wallet) this.apply(c.wallet);
      },
      error: (e) => this.loadError.set(toApiError(e).message || 'Connection problem'),
    });
  }

  apply(wallet: Pick<PlayWallet, 'balance' | 'version' | 'open_stakes'>): void {
    if (wallet.version < this.version) return;
    const changed = wallet.balance !== this.balance();
    this.version = wallet.version;
    this.balance.set(wallet.balance);
    // Frames from grants and transfers leave open stakes alone and do not send them.
    if (wallet.open_stakes != null) this.openStakes.set(wallet.open_stakes);
    this.loadError.set(null);
    if (changed) this.pulse.update((n) => n + 1);
  }

  private reset(): void {
    this.request?.unsubscribe();
    this.version = -1;
    this.balance.set(null);
    this.openStakes.set(null);
    this.loadError.set(null);
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
    realtime.on<FightBook>('book.updated').pipe(auditTime(500), takeUntilDestroyed(destroy)).subscribe((b) => {
      const mine = this.mine();
      if (b.match_id === this.matchId() && mine && BET_SIDES.some(side => Number(mine[side].stake) > 0)) this.reload();
    });
    realtime
      .on<{ match_id: string }>('bets.updated', 'bets.settled')
      .pipe(takeUntilDestroyed(destroy))
      .subscribe((m) => m.match_id === this.matchId() && this.reload());
    realtime.on('connected', 'betting.config_updated').pipe(takeUntilDestroyed(destroy)).subscribe(() => this.reload());
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
      this.notice.set(`Bet accepted · ${BET_LABEL[side]} ${formatArenaPoints(toCents(placed.bet.amount) ?? 0)}`);
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
    // A newer frame may already be on screen. Keep the personal position we have; the panel reprices it.
    const mine = this.mine();
    if (!mine || book === view.book) this.mine.set(view.mine);
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
    // Settlement changes results; the personal figures come from the server.
    if (book.state === 'settled' && current?.state !== 'settled') this.reload();
  }
}

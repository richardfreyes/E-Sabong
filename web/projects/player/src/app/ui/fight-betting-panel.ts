import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type { BetLimits, BetPosition, BetSide, BetStatus, FightBook } from '@shared/models';
import { ArenaPointsPipe, formatArenaPoints, toCents } from '@shared/pipes';
import { BET_CHIPS, BET_LABEL } from '../core/play-betting';
import { CountUpComponent } from './count-up';

export interface BetRequest {
  side: BetSide;
  /** Decimal string with two places, e.g. "100.00". */
  amount: string;
}

const RESULT_LABEL: Record<Exclude<BetStatus, 'accepted'>, string> = { won: 'Won', lost: 'Lost', refunded: 'Refunded' };
const TITLE: Record<BetSide, string> = { meron: 'Meron', wala: 'Wala', draw: 'Draw' };

/** "1000" -> "1K", "10000" -> "10K". */
function chipText(value: number): string {
  return value >= 1000 ? `${value / 1000}K` : String(value);
}

/** Keep digits and one decimal point with at most two places, as typed. */
function cleanAmount(raw: string): string {
  const [whole, ...rest] = raw.replace(/[^\d.]/g, '').split('.');
  const digits = whole.replace(/^0+(?=\d)/, '').slice(0, 9);
  return rest.length ? `${digits || '0'}.${rest.join('').slice(0, 2)}` : digits;
}

/** Integer cents -> the plain amount shown in the input ("10300", "100.35"). */
function plain(cents: number): string {
  return cents % 100 === 0 ? String(cents / 100) : (cents / 100).toFixed(2);
}

/**
 * The fight betting panel: live all-player pools, server-computed multipliers,
 * the player's own BET and RETURNS (play balance, never currency), the amount controls and the DRAW strip. It only displays what
 * the stores hold (server data) and emits bet requests; it never changes figures itself.
 */
@Component({
  selector: 'pp-fight-betting-panel',
  imports: [ArenaPointsPipe, CountUpComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './fight-betting-panel.scss',
  template: `
    @if (loading() && !book()) {
      <div class="skeleton" role="status" aria-label="Loading betting"></div>
    } @else if (loadFailed()) {
      <!-- No figures were received: say so, never show zeros in their place. -->
      <div class="unavailable" role="alert">
        <p><b>Betting figures are unavailable right now.</b> {{ error() }}</p>
        <button type="button" class="retry" (click)="retry.emit()">Try again</button>
      </div>
    } @else {
      <!-- The fight header's pill shows this state on screen; the strip stays as the screen-reader announcement. -->
      <div class="status">
        <span class="state" [class]="'state ' + tone()" role="status"><span class="dot" aria-hidden="true"></span>{{ stateLabel() }}</span>
        <span class="note">Simulated play · no cash value</span>
      </div>
      @if (!book()?.state) {
        <p class="hint">Betting has not opened for this fight yet. Pools and odds appear here as soon as it opens.</p>
      }
      @if (mine()?.settled_payout; as paid) {
        <p class="summary" role="status">Fight settled · Returned <b>{{ paid | arenaPoints }}</b> · Net <b>{{ net() }}</b></p>
      }

      <div class="cards">
        @for (side of pair; track side) {
          <button type="button" [class]="'bet ' + side" [class.mine]="hasStake(side)" [disabled]="!canBet()" (click)="submit(side)"
                  [attr.aria-label]="cardLabel(side)" [attr.aria-busy]="placing() === side" aria-describedby="pp-bet-help">
            <span class="head"><span class="name">{{ label[side] }}</span>@if (hasStake(side)) {<span class="tag">Your side</span>}</span>
            <span class="pool"><b data-field="pool" [style.--pool-characters]="(pool(side) | arenaPoints).length"><pp-count-up [value]="pool(side)" /></b><span>All players</span></span>
            <span class="rows">
              <span class="row odds"><span class="k">Odds =</span>
                <b data-field="rate">{{ rate(side) }}</b></span>
              <span class="row pill"><span class="k">Bet:</span><b data-field="bet">{{ stake(side) | arenaPoints }}</b></span>
              <span class="row pill soft"><span class="k">Pays:</span>
                <b class="pays" data-field="pays">{{ pays(side) | arenaPoints }}@if (result(side); as r) {<small [class]="r">{{ resultLabel[r] }}</small>}</b></span>
            </span>
            <span class="bar"><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>{{ placing() === side ? 'Placing…' : 'Bet ' + title[side] }}</span>
          </button>
        }
        <button type="button" class="bet draw" [class.mine]="hasStake('draw')" [disabled]="!canBet()" (click)="submit('draw')"
                [attr.aria-label]="cardLabel('draw')" [attr.aria-busy]="placing() === 'draw'" aria-describedby="pp-bet-help">
          <span class="head"><span class="name">DRAW</span>@if (hasStake('draw')) {<span class="tag">Your side</span>}</span>
          <span class="pool"><b data-field="draw-pool" [style.--pool-characters]="(pool('draw') | arenaPoints).length"><pp-count-up [value]="pool('draw')" /></b><span>All players</span></span>
          <span class="rows">
            <span class="row odds"><span class="k">Odds</span><b data-field="draw-rate">x{{ drawMultiplier() }}</b></span>
            <span class="row pill"><span class="k">B:</span><b data-field="draw-bet">{{ stake('draw') | arenaPoints }}</b></span>
            <span class="row pill soft"><span class="k">Pays:</span>
              <b class="pays"><span data-field="draw-pays">{{ pays('draw') | arenaPoints }}</span>@if (result('draw'); as r) {<small [class]="r">{{ resultLabel[r] }}</small>}</b></span>
          </span>
          <span class="bar"><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>@if (placing() === 'draw') {Placing…} @else {<span class="sr">Bet </span>Draw}</span>
        </button>
      </div>

      <section class="amount" aria-label="Bet amount">
        <div class="amount-row">
          <div class="field" [class.bad]="!!problem()">
            <label for="pp-bet-amount">Bet amount</label>
            <span class="coin" aria-hidden="true"></span>
            <input id="pp-bet-amount" type="text" inputmode="decimal" autocomplete="off" [value]="amount()" (input)="type($event)"
                   [attr.aria-invalid]="!!problem()" aria-describedby="pp-bet-help" />
          </div>
          <button type="button" class="allin" aria-label="All in" [disabled]="!book()?.accepting || allInValue() === null" (click)="allIn()"
                  [attr.title]="allInValue() === null ? 'Insufficient balance for the minimum bet' : null"><svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /></svg><span>All in<span aria-hidden="true">!</span></span></button>
        </div>
        <div class="purse">
          <span class="avail" title="Your balance is granted by the system for simulated play. No cash value; it cannot be bought, transferred, withdrawn or redeemed.">
            <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"><rect x="3" y="5" width="18" height="15" rx="2" /><path d="M16 12h5v4h-5a2 2 0 0 1 0-4z" /></svg>
            <span class="avail-text"><span class="lbl sr">Available Balance</span><b data-field="balance">{{ balance() | arenaPoints }}</b></span>
          </span>
          <div class="chips" role="group" aria-label="Quick amounts">
            @for (chip of chips; track chip; let i = $index) {
              <button type="button" [class]="'chip c' + i" [attr.aria-pressed]="isChip(chip)" [attr.aria-label]="chip | arenaPoints" (click)="choose(chip)">
                <svg aria-hidden="true" viewBox="0 0 200 200"><circle class="rim" cx="100" cy="100" r="92" /><circle class="face" cx="100" cy="100" r="62" />
                  <path class="notch" d="M88 8h24v30H88zM88 162h24v30H88zM8 88h30v24H8zM162 88h30v24h-30zM33 50l17-17 21 21-17 17zM129 54l21-21 17 17-21 21zM33 150l21-21 17 17-17 17zM129 146l17-17 21 21-17 17z" />
                  <circle class="ring" cx="100" cy="100" r="62" /></svg>
                <span>{{ chipText(chip) }}</span>
              </button>
            }
          </div>
        </div>
        <p id="pp-bet-help" class="help" [class.bad]="!!problem()" [class.sr]="!problem() && !blockedReason()" aria-live="polite">{{ problem() ?? blockedReason() ?? limitsText() }}</p>
      </section>

      <div aria-live="polite">
        @if (error(); as e) { <p class="error" role="alert">{{ e }}</p> }
        @else if (notice(); as n) { <p class="ok">{{ n }}</p> }
      </div>
    }
  `,
})
export class FightBettingPanelComponent {
  readonly book = input<FightBook | null>(null);
  readonly mine = input<BetPosition | null>(null);
  readonly limits = input<BetLimits | null>(null);
  readonly balance = input<string | null>(null);
  readonly placing = input<BetSide | null>(null);
  readonly loading = input(false);
  readonly notice = input<string | null>(null);
  readonly error = input<string | null>(null);
  readonly bet = output<BetRequest>();
  /** The panel could not load the fight's figures; the parent reloads them. */
  readonly retry = output<void>();

  readonly pair = ['meron', 'wala'] as const;
  readonly label = BET_LABEL;
  readonly title = TITLE;
  readonly chips = BET_CHIPS;
  readonly chipText = chipText;
  readonly resultLabel = RESULT_LABEL;

  readonly amount = signal('100');
  /** The typed amount in cents; null while empty or malformed. */
  readonly amountCents = computed(() => {
    const raw = this.amount();
    return /^\d+(\.\d{1,2})?$/.test(raw) ? toCents(raw) : null;
  });

  /** Quick feedback only: the server validates every bet again. */
  readonly problem = computed(() => {
    const cents = this.amountCents();
    if (cents === null) return this.amount() ? 'Enter a valid amount.' : 'Enter a bet amount.';
    if (cents <= 0) return 'Enter an amount above zero.';
    const limits = this.limits();
    const ap = (v: string) => formatArenaPoints(toCents(v) ?? 0);
    if (limits) {
      if (cents < (toCents(limits.min_bet) ?? 0)) return `The minimum bet is ${ap(limits.min_bet)}.`;
      if (cents > (toCents(limits.max_bet) ?? 0)) return `The maximum bet is ${ap(limits.max_bet)}.`;
      if (cents > (toCents(limits.remaining_fight_stake) ?? 0)) return `Only ${ap(limits.remaining_fight_stake)} left of this fight's limit.`;
    }
    const balance = this.balance();
    // Unknown is not zero and not unlimited: say so instead of guessing.
    if (balance === null) return this.book() ? 'Your balance could not be loaded. Try again shortly.' : null;
    if (cents > (toCents(balance) ?? 0)) return 'Insufficient balance.';
    return null;
  });

  readonly canBet = computed(() => !!this.book()?.accepting && !this.problem() && !this.placing());

  /** Nothing was received for this fight and the request failed: an error state, not empty figures. */
  readonly loadFailed = computed(() => !this.book() && !this.loading() && !!this.error());

  /** Why the bet buttons are disabled when the amount itself is fine; shown under the amount. */
  readonly blockedReason = computed(() => {
    const b = this.book();
    if (this.placing()) return 'Placing your bet…';
    if (!b?.state) return 'Betting has not opened for this fight.';
    if (b.state === 'settled') return 'This fight is settled; betting is over.';
    if (!b.accepting) return 'Betting is closed for this fight.';
    return null;
  });

  /** Balance capped by the max bet and the fight's remaining limit, in cents; null if under the minimum. */
  readonly allInValue = computed(() => {
    const balance = this.balance();
    const limits = this.limits();
    if (balance === null || !limits) return null;
    const cap = Math.min(toCents(balance) ?? 0, toCents(limits.max_bet) ?? 0, toCents(limits.remaining_fight_stake) ?? 0);
    return cap > 0 && cap >= (toCents(limits.min_bet) ?? 0) ? cap : null;
  });

  readonly limitsText = computed(() => {
    const l = this.limits();
    if (!l) return '';
    const ap = (v: string) => formatArenaPoints(toCents(v) ?? 0);
    return `Min ${ap(l.min_bet)} · Max ${ap(l.max_bet)} · ${ap(l.remaining_fight_stake)} left this fight`;
  });

  readonly provisional = computed(() => this.book()?.state === 'open');
  readonly stateLabel = computed(() => {
    const b = this.book();
    if (!b?.state) return 'Betting Closed';
    if (b.state === 'settled') return `Settled · ${this.outcomeLabel()}`;
    return b.accepting ? 'Betting Open' : 'Betting Closed';
  });
  readonly tone = computed(() => {
    const b = this.book();
    if (!b?.state) return 'idle';
    return b.state === 'settled' ? 'settled' : b.accepting ? 'open' : 'closed';
  });
  readonly outcomeLabel = computed(() => {
    const o = this.book()?.outcome;
    if (o === 'meron' || o === 'wala' || o === 'draw') return BET_LABEL[o];
    return o === 'no_contest' ? 'No contest' : o === 'cancelled' ? 'Cancelled' : '';
  });
  /** "8.00" -> "8", "7.50" -> "7.5". */
  readonly drawMultiplier = computed(() => {
    const m = this.book()?.draw_multiplier;
    return m ? String(Number(m)) : '—';
  });
  readonly net = computed(() => {
    const net = this.mine()?.settled_net;
    const cents = net === null || net === undefined ? null : toCents(net);
    if (cents === null) return '—';
    const text = formatArenaPoints(Math.abs(cents));
    return cents > 0 ? `+${text}` : cents < 0 ? `−${text}` : text;
  });

  /** Server pool total; null (shown as a dash) when no book was received. Never a substituted zero. */
  pool(side: BetSide): string | null {
    return this.book()?.pools[side] ?? null;
  }

  /** Server-computed multiplier, or a dash while it is unavailable (a side has no stake). */
  rate(side: 'meron' | 'wala'): string {
    const value = this.book()?.odds[side === 'meron' ? 'meron_display' : 'wala_display'];
    return value ? `${Number(value).toFixed(2)}×` : '—';
  }

  /** The player's own stake from the API; null (a dash) until their position has been received. */
  stake(side: BetSide): string | null {
    return this.mine()?.[side]?.stake ?? null;
  }

  /** Projected or settled return; null renders as a dash. */
  pays(side: BetSide): string | null {
    return this.mine()?.[side]?.pays ?? null;
  }

  /** The player has a stake on this side (from the API): the card shows "Your side". */
  hasStake(side: BetSide): boolean {
    const cents = toCents(this.stake(side) ?? '0');
    return cents !== null && cents > 0;
  }

  result(side: BetSide): Exclude<BetStatus, 'accepted'> | null {
    const r = this.mine()?.[side].result;
    return r && r !== 'accepted' ? r : null;
  }

  /** Real buttons carry the card's figures in their accessible name, not only the action. */
  cardLabel(side: BetSide): string {
    const cents = this.amountCents();
    const ap = (v: string | null) => (v === null ? 'unavailable' : formatArenaPoints(toCents(v) ?? 0));
    const action = `Bet ${cents === null ? '' : formatArenaPoints(cents) + ' '}on ${BET_LABEL[side]}`;
    const payout = side === 'draw' ? `${this.drawMultiplier()} times` : this.rate(side).replace('—', 'unavailable');
    return `${action}. All players ${ap(this.pool(side))}, odds ${payout}, your bet ${ap(this.stake(side))}, returns ${ap(this.pays(side))}`;
  }

  isChip(chip: number): boolean {
    return this.amountCents() === chip * 100;
  }

  type(event: Event): void {
    const input = event.target as HTMLInputElement;
    const clean = cleanAmount(input.value);
    input.value = clean;
    this.amount.set(clean);
  }

  choose(chip: number): void {
    this.amount.set(String(chip));
  }

  allIn(): void {
    const cents = this.allInValue();
    if (cents !== null) this.amount.set(plain(cents));
  }

  submit(side: BetSide): void {
    const cents = this.amountCents();
    if (!this.canBet() || cents === null) return;
    this.bet.emit({ side, amount: (cents / 100).toFixed(2) });
  }
}

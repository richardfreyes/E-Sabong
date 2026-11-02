import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type { BetLimits, BetPosition, BetSide, BetStatus, FightBook } from '@shared/models';
import { CREDITS_SHORT, CreditsPipe, formatCredits, toCents } from '@shared/pipes';
import { BET_CHIPS, BET_LABEL } from '../core/play-betting';

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
 * The fight betting panel: live all-player pools and their split, server-computed multipliers,
 * the player's own BET and RETURNS (in play credits, never currency), the amount controls and the DRAW strip. It only displays what
 * the stores hold (server data) and emits bet requests; it never changes figures itself.
 */
@Component({
  selector: 'pp-fight-betting-panel',
  imports: [CreditsPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './fight-betting-panel.scss',
  template: `
    @if (loading() && !book()) {
      <div class="skeleton" role="status" aria-label="Loading betting"></div>
    } @else {
      <div class="status">
        <span class="state" [class]="'state ' + tone()" role="status"><span class="dot" aria-hidden="true"></span>{{ stateLabel() }}</span>
        <span class="note">Play credits · no cash value</span>
      </div>
      @if (!book()?.state) {
        <p class="hint">Betting has not opened for this fight yet. Pools and odds appear here as soon as it opens.</p>
      }
      @if (mine()?.settled_payout; as paid) {
        <p class="summary" role="status">Fight settled · Returned <b>{{ paid | credits: true }}</b> · Net <b>{{ net() }}</b></p>
      }

      <div>
        <div class="split-labels">
          <span class="m">{{ split().meron }}</span><span class="mid">Pool split</span><span class="w">{{ split().wala }}</span>
        </div>
        <div class="split-bar" [class.empty]="split().empty" aria-hidden="true">
          <div class="m" [style.width.%]="split().width"></div><div class="w"></div>
        </div>
      </div>

      <div class="cards">
        @for (side of pair; track side) {
          <button type="button" [class]="'bet ' + side" [disabled]="!canBet()" (click)="submit(side)"
                  [attr.aria-label]="cardLabel(side)" [attr.aria-busy]="placing() === side">
            <span class="name">{{ label[side] }}</span>
            <span class="pool"><b data-field="pool" [style.--pool-characters]="(pool(side) | credits).length">{{ pool(side) | credits }}</b><span>Pool</span></span>
            <span class="rows">
              <span class="row"><span class="k">Odds</span>
                <b data-field="rate">{{ rate(side) }}@if (provisional() && rate(side) !== '—') {<small>est</small>}</b></span>
              <span class="row"><span class="k">Your bet</span><b data-field="bet">{{ stake(side) | credits }}</b></span>
              <span class="row"><span class="k">Returns</span>
                <b class="pays" data-field="pays">{{ pays(side) | credits }}@if (result(side); as r) {<small [class]="r">{{ resultLabel[r] }}</small>}</b></span>
            </span>
            <span class="bar"><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>{{ placing() === side ? 'Placing…' : 'Bet ' + title[side] }}</span>
          </button>
        }
      </div>

      <section class="amount" aria-label="Bet amount">
        <div class="amount-head">
          <label for="pp-bet-amount">Bet amount (play credits)</label>
          <span title="Play credits granted by the system for simulated betting. Separate from account points; no cash value, cannot be bought, withdrawn or redeemed.">Play credits <b data-field="balance">{{ balance() | credits }}</b></span>
        </div>
        <div class="amount-row">
          <div class="field" [class.bad]="!!problem()">
            <span class="cur" aria-hidden="true">{{ unit }}</span>
            <input id="pp-bet-amount" type="text" inputmode="decimal" autocomplete="off" [value]="amount()" (input)="type($event)"
                   [attr.aria-invalid]="!!problem()" aria-describedby="pp-bet-help" />
          </div>
          <button type="button" class="allin" [disabled]="!book()?.accepting || allInValue() === null" (click)="allIn()">All in</button>
        </div>
        <div class="chips" role="group" aria-label="Quick amounts">
          @for (chip of chips; track chip) {
            <button type="button" class="chip" [attr.aria-pressed]="isChip(chip)" [attr.aria-label]="chip | credits: true" (click)="choose(chip)">{{ chipText(chip) }}</button>
          }
        </div>
        <p id="pp-bet-help" class="help" [class.bad]="!!problem()" aria-live="polite">{{ problem() ?? limitsText() }}</p>
      </section>

      <button type="button" class="draw" [disabled]="!canBet()" (click)="submit('draw')" [attr.aria-label]="cardLabel('draw')"
              [attr.aria-busy]="placing() === 'draw'">
        <span class="t">
          <b>Draw <i data-field="draw-rate">{{ drawMultiplier() }}×</i></b>
          <span>Pool <span data-field="draw-pool">{{ pool('draw') | credits }}</span> · Your bet <span data-field="draw-bet">{{ stake('draw') | credits }}</span>
            · Returns <span data-field="draw-pays">{{ pays('draw') | credits }}</span>@if (result('draw'); as r) {<small [class]="r">{{ resultLabel[r] }}</small>}</span>
        </span>
        <span class="bar"><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>{{ placing() === 'draw' ? 'Placing…' : 'Draw' }}</span>
      </button>

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

  readonly pair = ['meron', 'wala'] as const;
  readonly label = BET_LABEL;
  readonly title = TITLE;
  readonly chips = BET_CHIPS;
  readonly chipText = chipText;
  readonly resultLabel = RESULT_LABEL;
  readonly unit = CREDITS_SHORT;

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
    const credits = (v: string) => formatCredits(toCents(v) ?? 0, true);
    if (limits) {
      if (cents < (toCents(limits.min_bet) ?? 0)) return `The minimum bet is ${credits(limits.min_bet)}.`;
      if (cents > (toCents(limits.max_bet) ?? 0)) return `The maximum bet is ${credits(limits.max_bet)}.`;
      if (cents > (toCents(limits.remaining_fight_stake) ?? 0)) return `Only ${credits(limits.remaining_fight_stake)} left of this fight's limit.`;
    }
    const balance = this.balance();
    if (balance !== null && cents > (toCents(balance) ?? 0)) return 'Not enough play credits.';
    return null;
  });

  readonly canBet = computed(() => !!this.book()?.accepting && !this.problem() && !this.placing());

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
    const credits = (v: string) => formatCredits(toCents(v) ?? 0);
    return `Min ${credits(l.min_bet)} · Max ${credits(l.max_bet)} · ${credits(l.remaining_fight_stake)} left this fight (credits)`;
  });

  /** MERON share of the MERON + WALA pool. No stakes yet: no percentages are invented. */
  readonly split = computed(() => {
    const meron = toCents(this.pool('meron')) ?? 0;
    const wala = toCents(this.pool('wala')) ?? 0;
    const total = meron + wala;
    if (total <= 0) return { meron: '—', wala: '—', width: 50, empty: true };
    const pct = (meron / total) * 100;
    return { meron: `${pct.toFixed(1)}%`, wala: `${(100 - pct).toFixed(1)}%`, width: pct, empty: false };
  });

  readonly provisional = computed(() => this.book()?.state === 'open');
  readonly stateLabel = computed(() => {
    const b = this.book();
    if (!b?.state) return 'Betting not open';
    if (b.state === 'settled') return `Settled · ${this.outcomeLabel()}`;
    return b.accepting ? 'Betting open' : 'Betting closed';
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
    const cents = toCents(this.mine()?.settled_net ?? '0') ?? 0;
    const text = formatCredits(Math.abs(cents));
    return cents > 0 ? `+${text}` : cents < 0 ? `−${text}` : text;
  });

  pool(side: BetSide): string {
    return this.book()?.pools[side] ?? '0.00';
  }

  /** Server-computed multiplier, or a dash while it is unavailable (a side has no stake). */
  rate(side: 'meron' | 'wala'): string {
    const value = this.book()?.odds[side === 'meron' ? 'meron_display' : 'wala_display'];
    return value ? `${Number(value).toFixed(2)}×` : '—';
  }

  stake(side: BetSide): string {
    return this.mine()?.[side].stake ?? '0.00';
  }

  /** null renders as a dash. */
  pays(side: BetSide): string | null {
    const position = this.mine()?.[side];
    return position ? position.pays : '0.00';
  }

  result(side: BetSide): Exclude<BetStatus, 'accepted'> | null {
    const r = this.mine()?.[side].result;
    return r && r !== 'accepted' ? r : null;
  }

  /** Real buttons carry the card's figures in their accessible name, not only the action. */
  cardLabel(side: BetSide): string {
    const cents = this.amountCents();
    const credits = (v: string | null) => (v === null ? 'unavailable' : formatCredits(toCents(v) ?? 0, true));
    const action = `Bet ${cents === null ? '' : formatCredits(cents, true) + ' '}on ${BET_LABEL[side]}`;
    const payout = side === 'draw' ? `${this.drawMultiplier()} times` : this.rate(side).replace('—', 'unavailable');
    return `${action}. Pool ${credits(this.pool(side))}, odds ${payout}, your bet ${credits(this.stake(side))}, returns ${credits(this.pays(side))}`;
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

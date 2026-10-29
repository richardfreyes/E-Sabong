import { Injectable, computed, signal } from '@angular/core';

export type PickSide = 'meron' | 'wala';

export interface PracticePick {
  side: PickSide;
  amount: number;
  at: string;
}

/** Quick-select values offered next to the numeric input. */
export const PICK_AMOUNTS = [50, 100, 500, 1000] as const;

const KEY = 'esb.player.picks';
const DEFAULT_AMOUNT = 100;
/** Picks are a local practice aid, so only the recent ones are worth keeping. */
const KEEP = 40;

/**
 * Which side a player marked for practice on a given fight, and the
 * demonstration amount alongside it.
 *
 * This is a per-device note only: nothing is sent to the API, no points move
 * when a side is chosen, and no points are awarded when a result is declared.
 * Demonstration points are not redeemable and are not a wager.
 */
@Injectable({ providedIn: 'root' })
export class PlayerPicks {
  private readonly state = signal<Record<string, PracticePick>>(this.read());

  readonly picks = this.state.asReadonly();
  /** The amount carried from one fight to the next, so it only needs setting once. */
  readonly amount = signal(DEFAULT_AMOUNT);

  readonly count = computed(() => Object.keys(this.state()).length);

  pickFor(matchId: string | null | undefined): PracticePick | null {
    return (matchId && this.state()[matchId]) || null;
  }

  choose(matchId: string, side: PickSide, amount: number): void {
    const pick: PracticePick = { side, amount, at: new Date().toISOString() };
    this.write({ ...this.state(), [matchId]: pick });
  }

  clear(matchId: string): void {
    const { [matchId]: _removed, ...rest } = this.state();
    this.write(rest);
  }

  private write(next: Record<string, PracticePick>): void {
    const entries = Object.entries(next).sort(([, a], [, b]) => (a.at < b.at ? 1 : -1));
    const trimmed = Object.fromEntries(entries.slice(0, KEEP));
    this.state.set(trimmed);
    try {
      localStorage.setItem(KEY, JSON.stringify(trimmed));
    } catch {
      /* storage unavailable: picks last for this visit */
    }
  }

  private read(): Record<string, PracticePick> {
    try {
      const raw = localStorage.getItem(KEY);
      return raw ? (JSON.parse(raw) as Record<string, PracticePick>) : {};
    } catch {
      return {};
    }
  }
}

import { Injectable, effect, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import type { Prediction, PredictionSide, PredictionStatus, TenantProfile } from '@shared/models';
import { AuthStore, PortalApi, RealtimeService, toApiError } from '@shared/services';

export type PickSide = PredictionSide;
export const PICK_SIDES: readonly PickSide[] = ['meron', 'wala', 'draw'];
export const PICK_LABEL: Record<PickSide, string> = { meron: 'MERON', wala: 'WALA', draw: 'DRAW' };

/** How a prediction fared against the official result. Display only: no points move either way. */
export type PickOutcome = Exclude<PredictionStatus, 'pending'>;
export const PICK_OUTCOME_LABEL: Record<PickOutcome, string> = { win: 'WIN', lose: 'LOSE', void: 'VOID' };

/** The decided outcome, or null while the result is not official yet. */
export function pickOutcome(prediction: Prediction | null | undefined): PickOutcome | null {
  return prediction && prediction.status !== 'pending' ? prediction.status : null;
}

/** Quick-select values offered next to the numeric input. */
export const PICK_AMOUNTS = [50, 100, 500, 1000] as const;

const DEFAULT_AMOUNT = 100;
/** Picks used to live only in this browser; the server is now the record. */
const LEGACY_KEY = 'esb.player.picks';

/**
 * The signed-in player's predictions, mirrored from the API so they follow the player to any
 * device. The server owns the window, the one-per-fight rule and every outcome; this cache only
 * ever replaces entries with canonical API responses, so repeated, missed or out-of-order
 * realtime messages cannot leave it wrong.
 *
 * Nothing here moves points. The amount is an on-screen practice figure: it is never sent or stored.
 */
@Injectable({ providedIn: 'root' })
export class PlayerPicks {
  private readonly api = inject(PortalApi);
  private readonly state = signal<Record<string, Prediction>>({});
  /** Latest request per fight; older responses are dropped. */
  private readonly sequence = new Map<string, number>();
  private readonly events = new Set<string>();

  readonly picks = this.state.asReadonly();
  readonly amount = signal(DEFAULT_AMOUNT);
  /** The fight whose prediction is being saved. */
  readonly saving = signal<string | null>(null);
  readonly error = signal<string | null>(null);

  constructor() {
    try {
      localStorage.removeItem(LEGACY_KEY);
    } catch {
      /* storage unavailable */
    }
    const auth = inject<AuthStore<TenantProfile>>(AuthStore);
    let owner: string | null | undefined;
    effect(() => {
      const id = auth.user()?.id ?? null;
      if (owner !== undefined && id !== owner) this.reset();
      owner = id;
    });
    const realtime = inject(RealtimeService);
    // A change from another device, or an official result: refetch only what is cached.
    realtime.on<{ match_id: string }>('prediction.updated').subscribe((m) => this.loadMatch(m.match_id));
    realtime
      .on<{ id: string }>('match.updated', 'match.result_declared', 'match.result_corrected')
      .subscribe((m) => m.id in this.state() && this.loadMatch(m.id));
    realtime.on('connected').subscribe(() => this.events.forEach((id) => this.loadEvent(id)));
  }

  pickFor(matchId: string | null | undefined): Prediction | null {
    return (matchId && this.state()[matchId]) || null;
  }

  /** Every prediction this player has on one event's card. */
  loadEvent(eventId: string): void {
    this.events.add(eventId);
    this.api.predictions({ event_id: eventId, page_size: 100 }).subscribe({
      next: (page) => this.state.update((s) => ({ ...s, ...Object.fromEntries(page.items.map((p) => [p.match_id, p])) })),
      error: () => undefined,
    });
  }

  loadMatch(matchId: string): void {
    const ticket = this.next(matchId);
    this.api.predictions({ match_id: matchId, page_size: 1 }).subscribe({
      next: (page) => this.sequence.get(matchId) === ticket && this.put(matchId, page.items[0] ?? null),
      error: () => undefined,
    });
  }

  async choose(matchId: string, side: PickSide): Promise<void> {
    const ticket = this.next(matchId);
    this.saving.set(matchId);
    this.error.set(null);
    try {
      const saved = await firstValueFrom(this.api.predict(matchId, side));
      if (this.sequence.get(matchId) === ticket) this.put(matchId, saved);
    } catch (err) {
      this.error.set(toApiError(err).message);
      this.loadMatch(matchId);
    } finally {
      if (this.saving() === matchId) this.saving.set(null);
    }
  }

  private next(matchId: string): number {
    const ticket = (this.sequence.get(matchId) ?? 0) + 1;
    this.sequence.set(matchId, ticket);
    return ticket;
  }

  private put(matchId: string, prediction: Prediction | null): void {
    this.state.update((s) => {
      const { [matchId]: _old, ...rest } = s;
      return prediction ? { ...rest, [matchId]: prediction } : rest;
    });
  }

  private reset(): void {
    this.state.set({});
    this.sequence.clear();
    this.events.clear();
    this.error.set(null);
  }
}

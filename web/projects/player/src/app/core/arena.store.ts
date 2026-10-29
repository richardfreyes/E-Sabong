import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, debounceTime } from 'rxjs';

import type { EventItem, Match, MatchOutcome } from '@shared/models';
import { PortalApi, RealtimeService, toApiError } from '@shared/services';
import { derbyStats, isSettled } from './match-phase';
import { PlayerPrefs } from './player-prefs';

import type { Announcement } from '../../../../../shared/arena/models';
export type { Announcement } from '../../../../../shared/arena/models';

interface MatchMessage {
  id: string;
  event_id: string;
  match_number: number;
  status: Match['status'];
  meron_name: string;
  wala_name: string;
  result: { outcome: MatchOutcome; revision: number } | null;
}

const ANNOUNCEMENT_MS = 7000;

/**
 * Live state of one event for the arena views: the fight on stage, the next
 * fight, the derby record and the result board. Provide it per component.
 *
 * WebSocket messages patch matches immediately; a debounced refetch then
 * reconciles details (timestamps, notes) from the API.
 */
@Injectable()
export class ArenaStore {
  private readonly api = inject(PortalApi);
  private readonly realtime = inject(RealtimeService);
  private readonly prefs = inject(PlayerPrefs);
  private readonly reconcile = new Subject<void>();
  private announcementTimer: ReturnType<typeof setTimeout> | null = null;
  private pinned = false;
  private readonly awaitingSnapshot = new Set<string>();

  readonly events = signal<EventItem[]>([]);
  readonly event = signal<EventItem | null>(null);
  readonly matches = signal<Match[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly announcement = signal<Announcement | null>(null);

  readonly eventId = computed(() => this.event()?.id ?? null);
  readonly isOpen = computed(() => ['live', 'scheduled'].includes(this.event()?.status ?? ''));

  /** In progress first; otherwise the next scheduled fight of an open event; otherwise the last one decided. */
  readonly current = computed<Match | null>(() => {
    const ms = this.matches();
    const live = ms.find((m) => m.status === 'in_progress');
    if (live) return live;
    const nextUp = ms.find((m) => m.status === 'scheduled');
    if (this.isOpen() && nextUp) return nextUp;
    return [...ms].reverse().find(isSettled) ?? nextUp ?? null;
  });

  readonly next = computed<Match | null>(() => {
    const cur = this.current();
    return this.matches().find((m) => m.status === 'scheduled' && m.id !== cur?.id && (!cur || m.match_number > cur.match_number)) ?? null;
  });

  /** Most recently decided fight other than the one on stage. */
  readonly previous = computed<Match | null>(() => {
    const cur = this.current();
    const decided = this.matches().filter((m) => m.result && m.id !== cur?.id);
    return decided.sort((a, b) => (b.result!.declared_at > a.result!.declared_at ? 1 : -1))[0] ?? null;
  });

  readonly stats = computed(() => derbyStats(this.matches()));

  constructor() {
    const destroy = inject(DestroyRef);
    this.reconcile.pipe(debounceTime(400), takeUntilDestroyed(destroy)).subscribe(() => this.loadMatches(false));
    this.realtime
      .on<MatchMessage>('match.updated', 'match.result_declared', 'match.result_corrected')
      .pipe(takeUntilDestroyed(destroy))
      .subscribe((msg) => this.onMatchMessage(msg));
    this.realtime
      .on<{ id: string }>('event.updated')
      .pipe(takeUntilDestroyed(destroy))
      .subscribe((msg) => (msg.id === this.eventId() ? this.loadEvent() : this.loadEvents()));
    destroy.onDestroy(() => this.announcementTimer && clearTimeout(this.announcementTimer));
  }

  /** Follows the most relevant event, or a specific one when `eventId` is given. */
  init(eventId: string | null = null): void {
    this.pinned = !!eventId;
    this.loading.set(true);
    this.loadEvents(eventId);
  }

  select(eventId: string): void {
    this.pinned = true;
    this.awaitingSnapshot.clear();
    const found = this.events().find((e) => e.id === eventId);
    if (found) this.event.set(found);
    this.loading.set(true);
    this.matches.set([]);
    this.loadEvent(eventId);
    this.loadMatches(true, eventId);
  }

  refresh(): void {
    if (this.prefs.liveUpdates() && this.realtime.state() !== 'open') {
      this.realtime.disconnect();
      this.realtime.connect();
    }
    this.loadEvents(this.pinned ? this.eventId() : null);
  }

  dismissAnnouncement(): void {
    this.announcement.set(null);
  }

  private loadEvents(preferId: string | null = null): void {
    this.api.events({ status: ['live', 'scheduled', 'completed'], page_size: 50 }).subscribe({
      next: (page) => {
        this.events.set(page.items);
        const target = (preferId && page.items.find((e) => e.id === preferId)) || (this.pinned && this.event()) || pickFeatured(page.items);
        if (!target && preferId) {
          // Pinned event outside the recent list (e.g. an older derby).
          this.loadEvent(preferId);
          this.loadMatches(true, preferId);
          return;
        }
        if (!target) {
          this.event.set(null);
          this.matches.set([]);
          this.loading.set(false);
          return;
        }
        const changed = target.id !== this.eventId();
        if (changed) this.awaitingSnapshot.clear();
        this.event.set(page.items.find((e) => e.id === target.id) ?? target);
        this.loadMatches(changed, target.id);
      },
      error: (e) => {
        this.error.set(toApiError(e).message);
        this.loading.set(false);
      },
    });
  }

  private loadEvent(id = this.eventId()): void {
    if (!id) return;
    this.api.event(id).subscribe({ next: (e) => this.event.set(e), error: (e) => this.error.set(toApiError(e).message) });
  }

  private loadMatches(showLoading: boolean, id = this.eventId()): void {
    if (!id) return;
    if (showLoading) this.loading.set(true);
    this.api.eventMatches(id).subscribe({
      next: (ms) => {
        if (id !== this.eventId() && this.eventId()) return;
        const existing = new Map(this.matches().map(m => [m.id, m]));
        for (const m of ms) this.awaitingSnapshot.delete(m.id);
        const pending = this.matches().filter(m => this.awaitingSnapshot.has(m.id) && !ms.some(item => item.id === m.id));
        this.matches.set([...ms.map(m => {
          const current = existing.get(m.id);
          // A request started before a result frame may resolve afterwards.
          // Preserve newer official revisions until the API catches up.
          if (current?.result && current.result.revision > (m.result?.revision ?? 0)) return current;
          if (current?.status === 'cancelled' && !isSettled(m)) return current;
          return m;
        }), ...pending].sort((a, b) => a.match_number - b.match_number));
        this.error.set(null);
        this.loading.set(false);
      },
      error: (e) => {
        this.error.set(toApiError(e).message);
        this.loading.set(false);
      },
    });
  }

  private onMatchMessage(value: unknown): void {
    if (!isMatchMessage(value)) return;
    const msg = value;
    if (msg.event_id !== this.eventId()) {
      if (!this.pinned && msg.status === 'in_progress' && !this.isOpen()) this.loadEvents();
      return;
    }
    const previous = this.matches().find(m => m.id === msg.id);
    if (previous) {
      if (previous.match_number !== msg.match_number) return;
      // Results are revisioned by the backend. Replayed or late frames must not
      // restore an older outcome, replay an announcement, or reopen a closed fight.
      if (previous.result && msg.result && msg.result.revision <= previous.result.revision) return;
      if (isSettled(previous) && !['completed', 'cancelled'].includes(msg.status)) return;
      if (previous.status === 'in_progress' && msg.status === 'scheduled') return;
      if (!msg.result && previous.status === msg.status && previous.meron_name === msg.meron_name && previous.wala_name === msg.wala_name) return;
    }
    // A completed match.updated frame may omit its result. Wait for the API
    // rather than constructing a completed record with an invented outcome.
    if (!previous && msg.status === 'completed' && !msg.result) {
      this.reconcile.next();
      return;
    }
    const result = msg.result
      ? { ...msg.result, notes: previous?.result?.notes ?? null, declared_at: previous?.result?.declared_at ?? new Date().toISOString() }
      : previous?.result ?? null;
    const updated: Match = {
      ...(previous ?? {
        id: msg.id, event_id: msg.event_id, event_name: this.event()?.name ?? null, match_number: msg.match_number,
        meron_details: null, wala_details: null, scheduled_at: null, started_at: null, ended_at: null, cancel_reason: null,
      }),
      status: msg.status, meron_name: msg.meron_name, wala_name: msg.wala_name, result,
    };
    if (!previous) this.awaitingSnapshot.add(msg.id);
    this.matches.update(list =>
      (previous ? list.map(m => m.id === msg.id ? updated : m) : [...list, updated]).sort((a, b) => a.match_number - b.match_number),
    );
    if (msg.result && this.prefs.announcements()) this.announce(msg, msg.result.revision > 1);
    this.reconcile.next();
  }

  private announce(msg: MatchMessage, corrected: boolean): void {
    if (this.announcementTimer) clearTimeout(this.announcementTimer);
    this.announcement.set({
      key: Date.now(),
      matchNumber: msg.match_number,
      outcome: msg.result!.outcome,
      corrected,
    });
    this.announcementTimer = setTimeout(() => this.announcement.set(null), ANNOUNCEMENT_MS);
  }
}

/** Live first, then the soonest upcoming, then the most recent closed event. */
export function pickFeatured(events: EventItem[]): EventItem | null {
  const byStart = (a: EventItem, b: EventItem) => Date.parse(a.starts_at) - Date.parse(b.starts_at);
  const live = events.filter((e) => e.status === 'live').sort(byStart);
  // With more than one derby live, follow the one actually under way.
  const started = live.filter((e) => Date.parse(e.starts_at) <= Date.now());
  if (started.length) return started.at(-1)!;
  if (live.length) return live[0];
  const upcoming = events.filter((e) => e.status === 'scheduled').sort(byStart);
  if (upcoming[0]) return upcoming[0];
  const closed = events.filter((e) => e.status === 'completed').sort(byStart);
  return closed.at(-1) ?? null;
}

/** Validate the existing FastAPI realtime_payload envelope at the player boundary. */
function isMatchMessage(value: unknown): value is MatchMessage {
  if (!value || typeof value !== 'object') return false;
  const msg = value as Partial<MatchMessage>;
  if (typeof msg.id !== 'string' || !msg.id || typeof msg.event_id !== 'string' || !msg.event_id) return false;
  if (!Number.isSafeInteger(msg.match_number) || msg.match_number! < 1) return false;
  if (!['scheduled', 'in_progress', 'completed', 'cancelled'].includes(msg.status ?? '')) return false;
  if (typeof msg.meron_name !== 'string' || typeof msg.wala_name !== 'string') return false;
  if (msg.result === null) return true;
  if (!msg.result || typeof msg.result !== 'object' || msg.status !== 'completed') return false;
  return ['meron', 'wala', 'draw', 'no_contest'].includes(msg.result.outcome)
    && Number.isSafeInteger(msg.result.revision) && msg.result.revision > 0;
}

import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, catchError, auditTime, filter, interval, of, switchMap, merge } from 'rxjs';
import type { EventItem, FightBookSummary, TenantProfile } from '@shared/models';
import { AuthStore, BettingControl, BrandingService, DeclaratorApi, DeclaratorMonitor, GhostAuto, MatchRecord, OnlinePlayers, PagedList, RealtimeService, refreshSnapshot, trailingExhaustMap, toApiError } from '@shared/services';

import { QuickBetSettingsApi, QuickBetSettings, defaultQuickBets } from '@shared/services';

/** An aggregate book frame: the ghost POST answer (with the ghost totals) or a book.updated message. */
export type BookFrame = Partial<Pick<FightBookSummary, 'state' | 'accepting' | 'pools' | 'odds' | 'final' | 'bet_count' | 'dummy_meron' | 'dummy_wala'>>
  & { match_id: string; version: number };

/** Realtime messages invalidate snapshots, or patch the current book when the frame carries all of it.
 *  switchMap rejects stale/out-of-order responses. */
@Injectable()
export class DeclaratorStore {
  readonly api = inject(DeclaratorApi);
  private readonly quickBetApi = inject(QuickBetSettingsApi);
  readonly quickBets = signal<QuickBetSettings>(defaultQuickBets());
  private readonly refreshQuickBets$ = new Subject<void>();
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly realtime = inject(RealtimeService);
  private readonly branding = inject(BrandingService);
  readonly liveEnabled = computed(() => this.branding.config()?.feature_flags?.['live_updates'] !== false);
  readonly events = signal<EventItem[]>([]);
  readonly eventId = signal<string | null>(null);
  readonly monitor = signal<DeclaratorMonitor | null>(null);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  /** The API refused this account (wrong account type or missing role), so nothing else can load. */
  readonly denied = signal(false);
  readonly approvalNotice = signal('');
  /** Automatic ghost betting on this event (null until loaded or for an API without it). */
  readonly ghost = signal<GhostAuto | null>(null);
  /** Players connected to the workspace right now. */
  readonly onlinePlayers = signal<number | null>(null);
  readonly onlineList = signal<OnlinePlayers | null>(null);
  private readonly refreshDesk$ = new Subject<void>();
  readonly records = new PagedList<MatchRecord>(q => this.api.records(q));
  private readonly refreshEvents$ = new Subject<void>();
  /** Set by a full refresh; a list-only reload (another event changed) leaves the desk alone. */
  private reselect = false;
  private readonly refreshMonitor$ = new Subject<void>();
  private readonly invalidate$ = new Subject<void>();
  private readonly refreshOnline$ = new Subject<void>();
  private readonly refreshLive$ = new Subject<void>();
  readonly approver = computed(() => this.auth.hasPermission('results.approve'));
  readonly submitter = computed(() => this.auth.user()?.account_type === 'declarator' && this.auth.hasPermission('results.submit'));
  readonly current = computed(() => this.monitor()?.current ?? null);
  /** Simulated betting on the current fight: pool totals, each player's stakes, odds and obligations. */
  readonly currentBook = computed(() => {
    const id = this.current()?.match.id;
    return this.monitor()?.books?.find(b => b.match_id === id) ?? null;
  });
  /** The server's betting switch for this event: the fight it acts on, OPEN/CLOSED, and what is allowed. */
  readonly betting = computed(() => this.monitor()?.betting ?? null);
  /** Settled fights on this card, newest fight first. */
  readonly settlements = computed(() => (this.monitor()?.books ?? []).filter(b => b.state === 'settled').sort((a, b) => b.match_number - a.match_number));
  readonly pending = computed(() => ['declared', 'pending_approval', 'under_review'].includes(this.current()?.workflow_status ?? ''));
  readonly canSubmit = computed(() => this.submitter() && !this.loading() && !this.error() && !this.pending()
    && this.current()?.match.status === 'in_progress' && this.monitor()?.event.status === 'live'
    && this.betting()?.status !== 'open');
  /** Loaded, allowed, and assigned to nothing. */
  readonly empty = computed(() => !this.loading() && !this.error() && !this.denied() && !this.events().length);
  /** Why the declaration buttons are disabled, when it is not obvious from the screen. */
  readonly submitHint = computed(() => {
    const monitor = this.monitor();
    const current = this.current();
    if (!this.submitter() || this.loading() || this.error() || !monitor) return null;
    if (this.pending() && current) return `Fight #${current.match.match_number} is awaiting approval.`;
    if (monitor.event.status !== 'live') return 'Declarations open when the event is live.';
    if (!current || current.match.status !== 'in_progress') {
      const ready = this.betting();
      if (ready?.match_number && ready.match_status === 'scheduled') return `Open betting to start Fight #${ready.match_number}.`;
      if (ready && !ready.match_id && ready.can_open) return 'Open betting to start the next fight.';
      return current?.match.status === 'scheduled' ? `Waiting for Fight #${current.match.match_number} to start.` : 'Waiting for the next fight to start.';
    }
    if (this.betting()?.status === 'open') return `Close betting to declare the result of Fight #${current.match.match_number}.`;
    if (this.betting()?.book_state === 'closed') return `Declare Meron, Wala, or Draw for Fight #${current.match.match_number}. Betting stays closed until then.`;
    return null;
  });

  constructor() {
    const destroy = inject(DestroyRef);
    this.refreshQuickBets$.pipe(switchMap(() => this.quickBetApi.get().pipe(catchError(() => of(null)))),
      takeUntilDestroyed(destroy)).subscribe(config => { if (config) this.quickBets.set(config); });
    this.realtime.on('declarator.settings_updated', 'connected').pipe(takeUntilDestroyed(destroy))
      .subscribe(() => this.refreshQuickBets$.next());
    this.refreshQuickBets$.next();
    this.refreshEvents$.pipe(
      trailingExhaustMap(() => this.api.events().pipe(catchError(e => {
        const error = toApiError(e);
        // A refusal is a configuration problem, not a transient failure; retrying cannot fix it.
        if (error.status === 403) this.denied.set(true);
        else this.error.set(error.status === 404
          ? 'The Declarator workspace is unavailable. Ask your administrator to update and restart the API, then try again.'
          : error.message);
        this.loading.set(false);
        return of(null);
      }))),
      takeUntilDestroyed(destroy),
    ).subscribe(events => {
      if (!events) return;
      this.denied.set(false);
      this.error.set(null);
      this.events.set(events);
      const selected = events.find(e => e.id === this.eventId()) ?? events.find(e => e.status === 'live') ?? events[0];
      const full = this.reselect;
      this.reselect = false;
      if (full || (selected?.id ?? null) !== this.eventId()) this.selectEvent(selected?.id ?? null);
    });
    refreshSnapshot(this.refreshMonitor$, () => this.eventId(), id => {
      if (!this.monitor()) this.loading.set(true);
      this.error.set(null);
      return id ? this.api.monitor(id).pipe(catchError(e => {
        this.error.set(toApiError(e).message); return of(null);
      })) : of(null);
    }).pipe(takeUntilDestroyed(destroy)).subscribe(snapshot => {
      this.afterSnapshot(snapshot);
      if (snapshot) this.monitor.update(old => {
        if (!old || old.event.id !== snapshot.event.id) return snapshot;
        return {...snapshot, books: snapshot.books?.map(book => {
          const prior = old.books?.find(b => b.match_id === book.match_id);
          return prior && prior.version > book.version ? prior : book;
        })};
      });
      this.loading.set(false);
    });
    // Realtime changes reload the snapshot; history and desk extras follow only if it shows they changed.
    this.invalidate$.pipe(auditTime(150), takeUntilDestroyed(destroy)).subscribe(() => this.refreshMonitor$.next());
    // Desk extras: ghost job status and the online player count. Failures keep the last value.
    // The job's run counters change on every tick without a message, so they are polled, but only
    // while a job runs; starting or stopping one publishes ghost.updated.
    const visible = () => !document.hidden;
    refreshSnapshot(merge(this.refreshDesk$, interval(15_000).pipe(filter(() => visible() && !!this.ghost()?.active))),
      () => this.eventId(), id => {
        return id ? this.api.ghostAuto(id).pipe(catchError(() => of(null))) : of(null);
      }).pipe(takeUntilDestroyed(destroy)).subscribe(ghost => this.ghost.set(ghost));
    // Presence has no message of its own: poll it, and reload it when player stakes change, at most every
    // 3 s (pools and odds already show each bet at once from the book frame; this list adds stakes and
    // balances per player, and reloading it per bet was a third of an idle desk's requests).
    refreshSnapshot(merge(this.refreshDesk$, this.refreshOnline$.pipe(auditTime(3000)), interval(15_000).pipe(filter(visible))),
      () => this.eventId(), id => {
        return id ? this.api.onlinePlayers(id).pipe(catchError(() => of(null))) : of(null);
      }).pipe(takeUntilDestroyed(destroy)).subscribe(online => { if (online) { this.onlinePlayers.set(online.players); this.onlineList.set(online); } });
    this.realtime.on<{event_id?: string}>('ghost.updated').pipe(
      filter(data => !data.event_id || data.event_id === this.eventId()), takeUntilDestroyed(destroy),
    ).subscribe(() => this.refreshDesk$.next());
    // Player rows (and the bet count) come only from this read; totals and odds were already applied
    // from the frame, so a burst of player bets costs one read per 750 ms rather than one per bet.
    refreshSnapshot(this.refreshLive$.pipe(auditTime(750)), () => this.current()?.match.id, id =>
      id ? this.api.liveBook(id).pipe(catchError(() => of(null))) : of(null),
    ).pipe(takeUntilDestroyed(destroy)).subscribe(book => {
      if (!book) return;
      this.monitor.update(m => {
        if (!m || m.current?.match.id !== book.match_id) return m;
        const old = m.books?.find(b => b.match_id === book.match_id);
        if (old && old.version > book.version) return m;
        if (old?.bet_count !== book.bet_count) this.refreshOnline$.next();
        return {...m, books: [...(m.books ?? []).filter(b => b.match_id !== book.match_id), book]};
      });
    });
    // Opening betting publishes only book.updated, so a book that changes state (opened/closed/settled)
    // or belongs to a fight the desk does not yet show as current reloads the canonical snapshot
    // (betting switch, card). Ghost amounts and odds changes on the current fight are applied from the
    // frame itself; only new player bets (a changed bet count) fetch the book, for its player rows.
    this.realtime.on<BookFrame & {event_id?: string}>('book.updated').pipe(
      filter(data => !data.event_id || data.event_id === this.eventId()), takeUntilDestroyed(destroy),
    ).subscribe(data => {
      const known = this.monitor()?.books?.find(b => b.match_id === data.match_id);
      const structural = (!!data.match_id && data.match_id !== this.current()?.match.id)
        || (data.state !== undefined && known?.state !== data.state);
      if (structural) this.invalidate$.next();
      else if (!this.applyBookFrame(data)) this.refreshLive$.next();
    });
    this.realtime.on<{event_id?: string}>('match.updated', 'match.result_declared', 'match.result_corrected',
      'declaration.updated', 'betting.closed', 'fight.settled').pipe(
        filter(data => !data.event_id || data.event_id === this.eventId()), takeUntilDestroyed(destroy),
      ).subscribe(() => this.invalidate$.next());
    this.realtime.on('connected', 'declaration.assignment_changed').pipe(takeUntilDestroyed(destroy)).subscribe(() => this.refresh());
    // Another event's change only reloads the picker; the selected event reloads its desk too.
    this.realtime.on<{id?: string}>('event.updated').pipe(takeUntilDestroyed(destroy)).subscribe(data => {
      if (!data.id || data.id === this.eventId()) this.refresh();
      else this.refreshEvents$.next();
    });
    this.realtime.on<{type?: string; title?: string}>('notification.created').pipe(takeUntilDestroyed(destroy)).subscribe(notification => {
      if (this.approver() && notification.type === 'result.approval_required' && typeof notification.title === 'string') {
        this.approvalNotice.set(notification.title);
      }
    });
    // Realtime carries every change while the socket is open, and a reconnect ('connected') reloads
    // everything. While it is down the desk polls the full snapshot instead; while it is up, a slow
    // canonical monitor read still repairs a message lost to a failed publish.
    interval(15_000).pipe(filter(() => visible() && this.realtime.state() !== 'open'), takeUntilDestroyed(destroy))
      .subscribe(() => this.refresh());
    interval(120_000).pipe(filter(() => visible() && this.realtime.state() === 'open'), takeUntilDestroyed(destroy))
      .subscribe(() => this.refreshMonitor$.next());
    this.refresh();
  }

  /** Apply an aggregate book frame (a ghost POST answer or a book.updated message) to the current fight
   *  without a request. Returns false when the frame needs the full book: a fight the desk does not hold,
   *  a state change, or new player bets (their rows are not in the frame; totals and odds still show
   *  at once).
   *
   *  While the book is open the player pools change only with the bet count, so with an unchanged count
   *  every pool difference is ghost money: ghost = known ghost + (new pool - known pool), in cents. The
   *  frame's own ghost totals (the Declarator's POST answer) win when present. Older or repeated
   *  versions change nothing and count as applied, so a WebSocket echo of our own click is free. */
  applyBookFrame(frame: BookFrame): boolean {
    let applied = false;
    this.monitor.update(m => {
      const old = m?.books?.find(b => b.match_id === frame.match_id);
      if (!m || !old || m.current?.match.id !== frame.match_id || typeof frame.version !== 'number') return m;
      if (frame.version <= old.version) { applied = true; return m; }
      if (frame.state !== old.state || !frame.pools || !frame.odds) return m;
      if (frame.bet_count !== old.bet_count) {
        // New player bets: show the totals and odds now; ghost money cannot be told apart from player
        // money in this frame, so the ghost split stays and the caller reloads the player rows. The
        // version is not advanced, so that reload (same or newer version) always replaces this.
        return { ...m, books: m.books!.map(b => b === old ? { ...old, pools: frame.pools!, odds: frame.odds!,
          accepting: frame.accepting ?? old.accepting } : b) };
      }
      const cents = (v: string | number | null | undefined) => Math.round(Number(v ?? 0) * 100);
      const ghost = (side: 'meron' | 'wala') => {
        const own = frame[`dummy_${side}`];
        if (own !== undefined && own !== null) return String(own);
        return (Math.max(0, cents(old[`dummy_${side}`]) + cents(frame.pools![side]) - cents(old.pools[side])) / 100).toFixed(2);
      };
      applied = true;
      const next = { ...old, pools: frame.pools, odds: frame.odds, accepting: frame.accepting ?? old.accepting,
        final: frame.final ?? old.final, version: frame.version, dummy_meron: ghost('meron'), dummy_wala: ghost('wala') };
      return { ...m, books: m.books!.map(b => b === old ? next : b) };
    });
    return applied;
  }
  selectEvent(id: string | null): void {
    const changed = id !== this.eventId();
    this.eventId.set(id);
    if (changed) { this.monitor.set(null); this.seen = null; }
    this.refreshMonitor$.next();
    this.refreshDesk$.next();
    if (changed) { this.ghost.set(null); this.onlinePlayers.set(null); this.onlineList.set(null); }
    if (changed) this.records.setFilter('event_id', id);
    else this.records.load();
  }
  /** Show the switch state the API just returned, then reload the canonical snapshot. */
  applyBetting(control: BettingControl): void {
    this.monitor.update(m => m ? { ...m, betting: control } : m);
    this.reconcile();
  }
  refresh(): void { this.reselect = true; this.refreshEvents$.next(); }
  /** After a desk action: reload the canonical snapshot now. The monitor read decides what the desk
   *  shows (betting switch, card, settlement), so it goes out alone; the history page and the desk extras
   *  (ghost job, online players) follow once it has answered, one read each however many changes arrived
   *  meanwhile. On a small API they would otherwise compete with it for CPU, and an overlapping history
   *  load is still computed by the server after the browser drops it. Overlapping monitor reads are
   *  merged by refreshSnapshot. */
  reconcile(): void {
    this.reloadExtras = true;
    this.refreshMonitor$.next();
  }
  private reloadExtras = false;
  /** What the last snapshot showed of the history rows (the card with its results and the current
   *  declaration) and of the desk extras' inputs (the current fight and its betting switch). */
  private seen: { history: string; desk: string } | null = null;
  /** Every snapshot: after a desk action reload history and extras; otherwise (a realtime change, the
   *  periodic check) reload only what the snapshot shows has changed. A ghost job starting or stopping
   *  has its own message (ghost.updated), and presence is polled. A failed read reloads what was due. */
  private afterSnapshot(snapshot: DeclaratorMonitor | null): void {
    const forced = this.reloadExtras;
    this.reloadExtras = false;
    if (!snapshot || snapshot.event.id !== this.eventId()) {
      if (forced) { this.records.load(); this.refreshDesk$.next(); }
      return;
    }
    const current = snapshot.current;
    const now = {
      history: JSON.stringify([snapshot.matches, current?.match.id, current?.workflow_status, current?.declaration?.status]),
      desk: JSON.stringify([current?.match.id, snapshot.betting?.status, snapshot.betting?.match_id]),
    };
    const before = this.seen;
    this.seen = now;
    if (forced || (before && before.history !== now.history)) this.records.load();
    if (forced || (before && before.desk !== now.desk)) this.refreshDesk$.next();
  }
  refreshLive(): void { this.refreshLive$.next(); }
  refreshDesk(): void { this.refreshDesk$.next(); }
}

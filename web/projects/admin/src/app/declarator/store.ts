import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, catchError, auditTime, filter, interval, of, switchMap, merge } from 'rxjs';
import type { EventItem, TenantProfile } from '@shared/models';
import { AuthStore, BettingControl, BrandingService, DeclaratorApi, DeclaratorMonitor, GhostAuto, MatchRecord, OnlinePlayers, PagedList, RealtimeService, refreshSnapshot, trailingExhaustMap, toApiError } from '@shared/services';

import { QuickBetSettingsApi, QuickBetSettings, defaultQuickBets } from '@shared/services';

/** Realtime messages only invalidate snapshots. switchMap rejects stale/out-of-order responses. */
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
      if (snapshot) this.monitor.update(old => {
        if (!old || old.event.id !== snapshot.event.id) return snapshot;
        return {...snapshot, books: snapshot.books?.map(book => {
          const prior = old.books?.find(b => b.match_id === book.match_id);
          return prior && prior.version > book.version ? prior : book;
        })};
      });
      this.loading.set(false);
    });
    this.invalidate$.pipe(auditTime(150), takeUntilDestroyed(destroy)).subscribe(() => {
      this.refreshMonitor$.next(); this.records.load(); this.refreshDesk$.next();
    });
    // Desk extras: ghost job status and the online player count. Failures keep the last value.
    refreshSnapshot(this.refreshDesk$, () => this.eventId(), id => {
      return id ? this.api.ghostAuto(id).pipe(catchError(() => of(null))) : of(null);
    }).pipe(takeUntilDestroyed(destroy)).subscribe(ghost => this.ghost.set(ghost));
    refreshSnapshot(merge(this.refreshDesk$, this.refreshOnline$.pipe(auditTime(1000))), () => this.eventId(), id => {
      return id ? this.api.onlinePlayers(id).pipe(catchError(() => of(null))) : of(null);
    }).pipe(takeUntilDestroyed(destroy)).subscribe(online => { if (online) { this.onlinePlayers.set(online.players); this.onlineList.set(online); } });
    this.realtime.on<{event_id?: string}>('ghost.updated').pipe(
      filter(data => !data.event_id || data.event_id === this.eventId()), takeUntilDestroyed(destroy),
    ).subscribe(() => this.refreshDesk$.next());
    refreshSnapshot(this.refreshLive$.pipe(auditTime(150)), () => this.current()?.match.id, id =>
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
    // Stake traffic on the current fight refreshes only its book. Opening betting publishes only
    // book.updated, so a book that changes state (opened/closed/settled) or belongs to a fight the
    // desk does not yet show as current reloads the canonical snapshot (betting switch, card).
    this.realtime.on<{event_id?: string; match_id?: string; state?: string | null}>('book.updated').pipe(
      filter(data => !data.event_id || data.event_id === this.eventId()), takeUntilDestroyed(destroy),
    ).subscribe(data => {
      const known = this.monitor()?.books?.find(b => b.match_id === data.match_id);
      const structural = (!!data.match_id && data.match_id !== this.current()?.match.id)
        || (data.state !== undefined && known?.state !== data.state);
      if (structural) this.invalidate$.next(); else this.refreshLive$.next();
    });
    interval(15_000).pipe(filter(() => !document.hidden), takeUntilDestroyed(destroy)).subscribe(() => this.refreshDesk$.next());
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
    // Canonical reconciliation also repairs missed messages after a failed publish.
    interval(30_000).pipe(filter(() => !document.hidden), takeUntilDestroyed(destroy)).subscribe(() => this.refresh());
    this.refresh();
  }
  selectEvent(id: string | null): void {
    const changed = id !== this.eventId();
    this.eventId.set(id);
    if (changed) this.monitor.set(null);
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
  reconcile(): void { this.invalidate$.next(); }
  refreshLive(): void { this.refreshLive$.next(); }
  refreshDesk(): void { this.refreshDesk$.next(); }
}

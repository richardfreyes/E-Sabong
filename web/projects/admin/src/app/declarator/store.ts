import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, catchError, debounceTime, filter, interval, of, switchMap, tap } from 'rxjs';
import type { EventItem, TenantProfile } from '@shared/models';
import { AuthStore, BrandingService, DeclaratorApi, DeclaratorMonitor, MatchRecord, PagedList, RealtimeService, toApiError } from '@shared/services';

/** Realtime messages only invalidate snapshots. switchMap rejects stale/out-of-order responses. */
@Injectable()
export class DeclaratorStore {
  readonly api = inject(DeclaratorApi);
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  readonly realtime = inject(RealtimeService);
  private readonly branding = inject(BrandingService);
  readonly liveEnabled = computed(() => this.branding.config()?.feature_flags?.['live_updates'] !== false);
  readonly events = signal<EventItem[]>([]);
  readonly eventId = signal<string | null>(null);
  readonly monitor = signal<DeclaratorMonitor | null>(null);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly approvalNotice = signal('');
  readonly records = new PagedList<MatchRecord>(q => this.api.records(q));
  private readonly refreshEvents$ = new Subject<void>();
  private readonly refreshMonitor$ = new Subject<void>();
  private readonly invalidate$ = new Subject<void>();
  readonly approver = computed(() => this.auth.hasPermission('results.approve'));
  readonly submitter = computed(() => this.auth.user()?.account_type === 'declarator' && this.auth.hasPermission('results.submit'));
  readonly current = computed(() => this.monitor()?.current ?? null);
  readonly pending = computed(() => ['declared', 'pending_approval', 'under_review'].includes(this.current()?.workflow_status ?? ''));
  readonly canSubmit = computed(() => this.submitter() && !this.loading() && !this.error() && !this.pending()
    && this.current()?.match.status === 'in_progress' && this.monitor()?.event.status === 'live');

  constructor() {
    const destroy = inject(DestroyRef);
    this.refreshEvents$.pipe(
      switchMap(() => this.api.events().pipe(catchError(e => {
        const error = toApiError(e);
        this.error.set(error.status === 404
          ? 'The Declarator workspace is unavailable. Ask your administrator to update and restart the API, then try again.'
          : error.message);
        this.loading.set(false);
        return of(null);
      }))),
      takeUntilDestroyed(destroy),
    ).subscribe(events => {
      if (!events) return;
      this.events.set(events);
      const selected = events.find(e => e.id === this.eventId()) ?? events.find(e => e.status === 'live') ?? events[0];
      this.selectEvent(selected?.id ?? null);
    });
    this.refreshMonitor$.pipe(
      tap(() => { this.loading.set(true); this.error.set(null); }),
      switchMap(() => {
        const id = this.eventId();
        return id ? this.api.monitor(id).pipe(catchError(e => {
          this.error.set(toApiError(e).message); return of(null);
        })) : of(null);
      }), takeUntilDestroyed(destroy),
    ).subscribe(snapshot => { this.monitor.set(snapshot); this.loading.set(false); });
    this.invalidate$.pipe(debounceTime(150), takeUntilDestroyed(destroy)).subscribe(() => {
      this.refreshMonitor$.next(); this.records.load();
    });
    this.realtime.on<{event_id?: string}>('match.updated', 'match.result_declared', 'match.result_corrected',
      'declaration.updated').pipe(takeUntilDestroyed(destroy)).subscribe(() => this.invalidate$.next());
    this.realtime.on('connected', 'event.updated', 'declaration.assignment_changed').pipe(takeUntilDestroyed(destroy)).subscribe(() => this.refresh());
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
    if (changed) this.records.setFilter('event_id', id);
    else this.records.load();
  }
  refresh(): void { this.refreshEvents$.next(); }
  reconcile(): void { this.invalidate$.next(); }
}

import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatTooltipModule } from '@angular/material/tooltip';
import { firstValueFrom, interval } from 'rxjs';

import { type PlaybackSource, VideoStageComponent } from '@shared/arena';
import {
  ConfirmService,
  EmptyStateComponent,
  PageHeaderComponent,
  SecretRevealComponent,
  StatCardComponent,
  StatusChipComponent,
} from '@shared/components';
import type { EventItem, LiveStream, LiveStreamWithKey, StreamHealth } from '@shared/models';
import { AdminApi, RealtimeService, ToastService, toApiError } from '@shared/services';

const STATUS_LABEL: Record<EventItem['status'], string> = {
  draft: 'Draft', scheduled: 'Scheduled', live: 'Live', completed: 'Completed', cancelled: 'Cancelled',
};

/** Local calendar day (YYYY-MM-DD) of a timestamp, as the admin sees it. */
function localDay(value: string | Date): string {
  const d = typeof value === 'string' ? new Date(value) : value;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const SOURCE_LABEL: Record<LiveStream['source_kind'], string> = {
  obs: 'OBS / encoder (RTMP or WHIP)',
  external: 'Authorized external source',
  test: 'Test pattern (development)',
};

/**
 * Live streams for the arena display. Players see a stream only once it is enabled; until then
 * administrators can preview it here and assigned Declarators can monitor it.
 */
@Component({
  selector: 'app-streams-page',
  imports: [
    DatePipe, DecimalPipe, ReactiveFormsModule, MatButtonModule, MatCheckboxModule, MatFormFieldModule, MatIconModule,
    MatInputModule, MatSelectModule, MatSlideToggleModule, MatTooltipModule, PageHeaderComponent, StatCardComponent,
    StatusChipComponent, EmptyStateComponent, SecretRevealComponent, VideoStageComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; }
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .panel { border: 1px solid var(--border); border-radius: 1rem; background: var(--surface); padding: 16px; margin-bottom: 16px; }
    .panel h2 { margin: 0 0 4px; font-size: 1rem; font-weight: 600; }
    .muted { color: var(--text-secondary); font-size: 0.875rem; line-height: 1.6; margin: 0; }
    .form { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0 16px; margin-top: 12px; }
    .row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
    .grow { flex: 1; }
    .stream-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
    .stream-head h3 { margin: 0; font-size: 1rem; font-weight: 600; }
    .facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px 16px; margin: 12px 0; font-size: 0.8125rem; }
    .facts dt { color: var(--text-secondary); }
    .facts dd { margin: 0; overflow-wrap: anywhere; }
    code { font-size: 0.8rem; }
    .controls { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0 16px; align-items: center; }
    .preview { margin-top: 12px; max-width: 880px; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    .secret { margin: 12px 0; display: grid; gap: 8px; }
    .list-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin: 4px 0 12px; }
    .list-head h2 { margin: 0; font-size: 1rem; font-weight: 600; }
  `,
  template: `
    <app-page-header eyebrow="Operations" title="Live streaming"
      subtitle="Pick the arena date, attach a stream to that day's event (or to every event) and players watch it in the arena." />

    @if (health(); as h) {
      <div class="stats">
        <app-stat-card label="Streaming server" icon="dns" [tone]="h.reachable ? 'success' : 'warning'"
          [value]="!h.configured ? 'Not configured' : h.reachable ? 'Online' : 'Unreachable'" [hint]="h.detail" />
        <app-stat-card label="Live now" icon="sensors" [value]="h.live + ' of ' + h.streams" />
        <app-stat-card label="Viewers" icon="visibility" [value]="h.viewers"
          [hint]="h.webrtc_sessions + ' WebRTC · ' + h.hls_sessions + ' HLS'" />
        <app-stat-card label="Outbound" icon="upload" [value]="h.outbound_kbps === null ? '—' : (h.outbound_kbps / 1000 | number: '1.1-1') + ' Mbps'" />
      </div>
    }

    @if (health() && !health()!.configured) {
      <app-empty-state icon="videocam_off" title="Live streaming is not set up on this server"
        message="Ask the platform operator to start the streaming service (MediaMTX) and set MEDIAMTX_API_PASSWORD." />
    } @else {
      <section class="panel" aria-labelledby="new-stream">
        <h2 id="new-stream">Add a stream</h2>
        <p class="muted">Choose the date and the arena players will watch it in. Only broadcast video you are authorized to use.</p>
        <form class="form" [formGroup]="form" (ngSubmit)="create()">
          <mat-form-field><mat-label>Stream name</mat-label><input matInput formControlName="name" maxlength="120" placeholder="e.g. Arena camera" /></mat-form-field>
          <mat-form-field>
            <mat-label>Date</mat-label>
            <input matInput type="date" [value]="date()" (change)="setDate($any($event.target).value)" aria-label="Arena date" />
            <mat-hint>{{ isToday() ? 'Today' : (date() | date: 'EEEE') }} · {{ eventsOnDate().length }} event(s)</mat-hint>
          </mat-form-field>
          <mat-form-field>
            <mat-label>Arena (event)</mat-label>
            <mat-select formControlName="assignment">
              @for (e of eventsOnDate(); track e.id) { <mat-option [value]="e.id">{{ eventLabel(e) }}</mat-option> }
              <mat-option value="default">Every event (default arena stream)</mat-option>
              <mat-option value="none">Not assigned yet</mat-option>
            </mat-select>
            @if (!eventsOnDate().length) { <mat-hint>No event on this date: create it under Events, or use “Every event”.</mat-hint> }
          </mat-form-field>
          @if (sourceChoices() > 1) {
          <mat-form-field>
            <mat-label>Source</mat-label>
            <mat-select formControlName="source_kind">
              <mat-option value="obs">{{ sourceLabel.obs }}</mat-option>
              <mat-option value="external">{{ sourceLabel.external }}</mat-option>
              @if (health()?.test_source_available) { <mat-option value="test">{{ sourceLabel.test }}</mat-option> }
            </mat-select>
          </mat-form-field>
          }
          @if (form.controls.source_kind.value === 'external') {
            <mat-form-field>
              <mat-label>Source URL</mat-label>
              <input matInput formControlName="source_url" type="password" autocomplete="off"
                placeholder="https://…/index.m3u8, rtmps://…, wheps://…" />
              <mat-hint>Stored encrypted; never shown again or sent to players.</mat-hint>
            </mat-form-field>
          }
          <div class="row">
            <mat-checkbox formControlName="enabled">Visible to players</mat-checkbox>
            <span class="grow"></span>
            <button mat-flat-button type="submit" [disabled]="busy() || form.invalid">Add stream</button>
          </div>
        </form>
        @if (revealed(); as r) {
          <div class="secret" role="status">
            <app-secret-reveal label="Publish key for {{ r.name }} (OBS: Settings › Stream › Stream key)" [value]="r.obs_stream_key ?? ''" />
            <p class="muted">RTMP server: <code>{{ r.ingest?.rtmp_server ?? 'set STREAM_RTMP_URL' }}</code> · WHIP: <code>{{ origin }}{{ r.ingest?.whip_path }}</code> with Bearer token <code>{{ r.publish_key }}</code></p>
            <button mat-button type="button" (click)="revealed.set(null)">I stored the key</button>
          </div>
        }
      </section>

      @if (!streams().length && loaded()) {
        <app-empty-state icon="live_tv" title="No streams yet" message="Add a stream above, then broadcast to it from the Operator console or OBS." />
      } @else if (loaded()) {
        <div class="list-head">
          <h2>Streams for {{ isToday() ? 'today' : (date() | date: 'mediumDate') }}</h2>
          <span class="grow"></span>
          @if (hiddenCount()) {
            <mat-slide-toggle [checked]="showAll()" (change)="showAll.set($event.checked)">Show all streams ({{ hiddenCount() }} on other dates)</mat-slide-toggle>
          }
        </div>
        @if (!visibleStreams().length) {
          <p class="muted">No stream for this date. Add one above, or switch “Show all streams” on.</p>
        }
      }
      @for (s of visibleStreams(); track s.id) {
        <article class="panel" [attr.aria-label]="'Stream ' + s.name">
          <div class="stream-head">
            <h3>{{ s.name }}</h3>
            <app-status-chip [status]="s.status" />
            @if (s.enabled) { <app-status-chip status="active" text="Visible to players" /> } @else { <app-status-chip status="draft" text="Hidden from players" /> }
            <span class="grow"></span>
            <span class="muted" matTooltip="Players and monitors watching now">{{ s.viewers }} watching</span>
          </div>
          @if (s.status_detail) { <p class="muted">{{ s.status_detail }}</p> }
          <dl class="facts">
            <div><dt>Source</dt><dd>{{ sourceLabel[s.source_kind] }}@if (s.source_host) { · {{ s.source_protocol }} {{ s.source_host }}}</dd></div>
            <div><dt>Arena</dt><dd>{{ s.is_default ? 'Every event (default)' : s.event_id ? eventLabel(eventById().get(s.event_id) ?? null, s.event_name) : 'Not assigned' }}</dd></div>
            <div><dt>Media</dt><dd>{{ s.tracks?.join(' + ') || '—' }}@if (s.status === 'live') { · {{ s.webrtc_ready ? 'WebRTC ready' : 'HLS only for now' }}}</dd></div>
            <div><dt>Last live</dt><dd>{{ s.last_live_at ? (s.last_live_at | date: 'medium') : 'Never' }}</dd></div>
            @if (s.ingest; as ing) {
              <div><dt>RTMP server</dt><dd><code>{{ ing.rtmp_server ?? 'not configured' }}</code></dd></div>
              <div><dt>Stream key</dt><dd><code>{{ ing.stream_key_format }}</code> · key ends …{{ s.publish_key_hint }}</dd></div>
              <div><dt>WHIP (OBS 30+)</dt><dd><code>{{ origin }}{{ ing.whip_path }}</code></dd></div>
            }
          </dl>
          <div class="controls">
            <mat-form-field>
              <mat-label>Arena (event)</mat-label>
              <mat-select [value]="s.is_default ? 'default' : s.event_id ?? 'none'" (selectionChange)="assign(s, $event.value)">
                @for (e of eventChoices(s); track e.id) { <mat-option [value]="e.id">{{ eventLabel(e) }}</mat-option> }
                <mat-option value="default">Every event (default arena stream)</mat-option>
                <mat-option value="none">Not assigned</mat-option>
              </mat-select>
            </mat-form-field>
            <mat-slide-toggle [checked]="s.enabled" (change)="toggle(s, $event.checked)">Visible to players</mat-slide-toggle>
          </div>
          <div class="actions">
            <button mat-stroked-button type="button" (click)="preview(s)"><mat-icon>play_circle</mat-icon>{{ previewing() === s.id ? 'Reload preview' : 'Test playback' }}</button>
            <button mat-stroked-button type="button" (click)="reconnect(s)" matTooltip="Re-pull the source, or drop the OBS connection so it reconnects"><mat-icon>sync</mat-icon>Reconnect</button>
            @if (s.source_kind === 'obs') { <button mat-stroked-button type="button" (click)="rotate(s)"><mat-icon>key</mat-icon>New publish key</button> }
            <button mat-button type="button" class="destructive-action" (click)="remove(s)"><mat-icon>delete</mat-icon>Delete</button>
          </div>
          @if (previewing() === s.id) {
            <div class="preview">
              @if (previewSource()) {
                <pp-video-stage [playbackSource]="previewSource()" [requireMatch]="false" />
              } @else {
                <p class="muted" role="status">{{ previewMessage() }}</p>
              }
            </div>
          }
        </article>
      }
    }
  `,
})
export class StreamsPage {
  private readonly api = inject(AdminApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly destroyRef = inject(DestroyRef);
  readonly sourceLabel = SOURCE_LABEL;
  readonly origin = typeof location === 'undefined' ? '' : location.origin;
  readonly streams = signal<LiveStream[]>([]);
  readonly health = signal<StreamHealth | null>(null);
  readonly events = signal<EventItem[]>([]);
  /** Arena date (local), today by default: the event list and the stream list follow it. */
  readonly date = signal(localDay(new Date()));
  readonly showAll = signal(false);
  readonly isToday = computed(() => this.date() === localDay(new Date()));
  readonly eventById = computed(() => new Map(this.events().map((e) => [e.id, e] as const)));
  /** Events of the selected date (cancelled ones left out), live first then by start time. */
  readonly eventsOnDate = computed(() =>
    this.events()
      .filter((e) => e.status !== 'cancelled' && localDay(e.starts_at) === this.date())
      .sort((a, b) => Number(b.status === 'live') - Number(a.status === 'live') || a.starts_at.localeCompare(b.starts_at)),
  );
  private readonly onDate = (s: LiveStream) => {
    if (s.is_default || !s.event_id) return true;
    const e = this.eventById().get(s.event_id);
    return !!e && localDay(e.starts_at) === this.date();
  };
  readonly visibleStreams = computed(() => (this.showAll() ? this.streams() : this.streams().filter(this.onDate)));
  readonly hiddenCount = computed(() => this.streams().filter((s) => !this.onDate(s)).length);
  readonly sourceChoices = computed(() => (this.health()?.test_source_available ? 3 : 2));
  private eventsLoadedAt = 0;
  readonly loaded = signal(false);
  readonly busy = signal(false);
  readonly revealed = signal<LiveStreamWithKey | null>(null);
  readonly previewing = signal<string | null>(null);
  readonly previewSource = signal<PlaybackSource | null>(null);
  readonly previewMessage = signal('');
  readonly liveCount = computed(() => this.streams().filter((s) => s.status === 'live').length);

  readonly form = inject(FormBuilder).nonNullable.group({
    name: ['', [Validators.required, Validators.maxLength(120)]],
    source_kind: 'obs' as LiveStream['source_kind'],
    source_url: '',
    assignment: 'default',
    // New streams reach players straight away (the arena shows "Offline" until the broadcast starts).
    enabled: true,
  });

  constructor() {
    void this.load();
    void this.loadEvents();
    // Status and viewer counts change on their own; keep the page current while it is open.
    interval(5000).pipe(takeUntilDestroyed()).subscribe(() => void this.load());
    inject(RealtimeService).on('video.stream_status').pipe(takeUntilDestroyed()).subscribe(() => void this.load());
  }

  /** Events for the date picker; refreshed now and then so a newly created event shows up. */
  async loadEvents(force = false): Promise<void> {
    if (!force && Date.now() - this.eventsLoadedAt < 30_000 && this.events().length) return;
    try {
      const page = await firstValueFrom(this.api.events({ page_size: 100 }));
      this.eventsLoadedAt = Date.now();
      const first = !this.events().length;
      this.events.set(page.items);
      if (first && !this.form.controls.assignment.dirty) this.form.controls.assignment.setValue(this.defaultAssignment());
    } catch {
      /* the selects keep their last list */
    }
  }

  setDate(value: string): void {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return;
    this.date.set(value);
    this.form.controls.assignment.setValue(this.defaultAssignment());
    void this.loadEvents(true);
  }

  /** The selected date's live (else first) event; "every event" when that day has none. */
  private defaultAssignment(): string {
    return this.eventsOnDate()[0]?.id ?? 'default';
  }

  /** "MAIN · 8:00 AM · Live", with the date when it is not the selected one. */
  eventLabel(e: EventItem | null, fallback?: string | null): string {
    if (!e) return fallback ?? 'Unknown event';
    const at = new Date(e.starts_at);
    const time = at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    const day = localDay(at) === this.date() ? '' : ` · ${at.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`;
    return `${e.name} · ${time}${day} · ${STATUS_LABEL[e.status]}`;
  }

  /** The date's events, plus the stream's current event when it is on another date. */
  eventChoices(s: LiveStream): EventItem[] {
    const list = this.eventsOnDate();
    const current = s.event_id ? this.eventById().get(s.event_id) : undefined;
    return current && !list.includes(current) ? [current, ...list] : list;
  }

  async load(): Promise<void> {
    void this.loadEvents();
    try {
      const [streams, health] = await Promise.all([firstValueFrom(this.api.streams()), firstValueFrom(this.api.streamHealth())]);
      this.streams.set(streams);
      this.health.set(health);
    } catch (err) {
      if (!this.loaded()) this.toast.error(err);
    } finally {
      this.loaded.set(true);
    }
  }

  private assignment(value: string): Record<string, unknown> {
    if (value === 'default') return { is_default: true, event_id: null };
    if (value === 'none') return { is_default: false, event_id: null };
    return { is_default: false, event_id: value };
  }

  async create(): Promise<void> {
    const v = this.form.getRawValue();
    this.busy.set(true);
    try {
      const created = await firstValueFrom(
        this.api.createStream({
          name: v.name,
          source_kind: v.source_kind,
          source_url: v.source_kind === 'external' ? v.source_url : undefined,
          enabled: v.enabled,
          ...this.assignment(v.assignment),
        }),
      );
      this.revealed.set(created.publish_key ? created : null);
      this.form.reset({ name: '', source_kind: 'obs', source_url: '', assignment: this.defaultAssignment(), enabled: true });
      this.toast.success(`Stream “${created.name}” added`);
      await this.load();
    } catch (err) {
      this.toast.error(toApiError(err));
    } finally {
      this.busy.set(false);
    }
  }

  async assign(s: LiveStream, value: string): Promise<void> {
    await this.update(s, this.assignment(value), 'Arena assignment saved');
  }

  async toggle(s: LiveStream, enabled: boolean): Promise<void> {
    await this.update(s, { enabled }, enabled ? 'Players can now watch this stream' : 'Stream hidden from players');
  }

  private async update(s: LiveStream, body: Record<string, unknown>, done: string): Promise<void> {
    try {
      await firstValueFrom(this.api.updateStream(s.id, body));
      this.toast.success(done);
    } catch (err) {
      this.toast.error(toApiError(err));
    }
    await this.load();
  }

  async preview(s: LiveStream): Promise<void> {
    this.previewing.set(s.id);
    this.previewSource.set(null);
    this.previewMessage.set('Requesting playback…');
    try {
      const result = await firstValueFrom(this.api.previewStream(s.id));
      this.previewSource.set(result.playback);
      if (!result.playback) this.previewMessage.set(result.stream?.detail ?? result.message);
    } catch (err) {
      this.previewMessage.set(toApiError(err).message);
    }
  }

  async reconnect(s: LiveStream): Promise<void> {
    try {
      await firstValueFrom(this.api.reconnectStream(s.id));
      this.toast.info(s.source_kind === 'obs' ? 'Broadcaster disconnected; OBS reconnects automatically' : 'Reconnecting to the source');
    } catch (err) {
      this.toast.error(toApiError(err));
    }
    await this.load();
  }

  async rotate(s: LiveStream): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Issue a new publish key?',
      message: 'The current key stops working immediately and the broadcaster using it is disconnected.',
      confirmText: 'New key',
      destructive: true,
    });
    if (ok === null) return;
    try {
      this.revealed.set(await firstValueFrom(this.api.rotateStreamKey(s.id)));
    } catch (err) {
      this.toast.error(toApiError(err));
    }
    await this.load();
  }

  async remove(s: LiveStream): Promise<void> {
    const ok = await this.confirm.ask({
      title: `Delete “${s.name}”?`,
      message: 'Viewers and the broadcaster are disconnected and the publish key stops working.',
      confirmText: 'Delete stream',
      destructive: true,
    });
    if (ok === null) return;
    try {
      await firstValueFrom(this.api.deleteStream(s.id));
      if (this.previewing() === s.id) this.previewing.set(null);
      this.toast.success('Stream deleted');
    } catch (err) {
      this.toast.error(toApiError(err));
    }
    await this.load();
  }
}

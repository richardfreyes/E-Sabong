import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatTooltipModule } from '@angular/material/tooltip';
import { RouterLink } from '@angular/router';
import { firstValueFrom, interval } from 'rxjs';

import {
  ConfirmService,
  EmptyStateComponent,
  FieldErrorComponent,
  PageHeaderComponent,
  StatCardComponent,
  StatusChipComponent,
} from '@shared/components';
import type { Broadcast, OperatorAccount, OperatorsOverview } from '@shared/models';
import { RelativeTimePipe } from '@shared/pipes';
import { AdminApi, OperatorApi, RealtimeService, ToastService, toApiError } from '@shared/services';

import { TempPasswordDialog } from './user-detail';

const METHOD: Record<Broadcast['method'], string> = { browser: 'Browser camera', obs: 'OBS / encoder', external: 'External source', test: 'Test pattern' };

/**
 * Stream Operator accounts: create, activate/deactivate, reset passwords, assign the streams each
 * may broadcast and record, and watch active streaming sessions and recordings.
 */
@Component({
  selector: 'app-operators-page',
  imports: [
    DatePipe, RelativeTimePipe, RouterLink, ReactiveFormsModule, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule,
    MatSelectModule, MatTooltipModule, PageHeaderComponent, StatCardComponent, StatusChipComponent, EmptyStateComponent, FieldErrorComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; }
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .panel { border: 1px solid var(--border); border-radius: 1rem; background: var(--surface); padding: 16px; margin-bottom: 16px; }
    .panel h2 { margin: 0 0 4px; font-size: 1rem; font-weight: 600; }
    .muted { color: var(--text-secondary); font-size: .875rem; line-height: 1.6; margin: 0; }
    .form { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0 16px; margin-top: 12px; align-items: start; }
    .form .submit { display: flex; align-items: center; height: 56px; }
    .table-wrap { overflow-x: auto; margin: 12px -16px 0; }
    table { width: 100%; border-collapse: collapse; font-size: .85rem; }
    th, td { padding: 10px 16px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: middle; }
    th { font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; color: var(--text-secondary); font-weight: 600; white-space: nowrap; }
    .who strong { display: block; } .who span { color: var(--text-secondary); font-size: .78rem; }
    .assign { min-width: 220px; width: 100%; }
    .row-actions { display: flex; flex-wrap: wrap; gap: 4px; justify-content: flex-end; }
    .live { display: inline-flex; align-items: center; gap: 6px; color: var(--error-text); font-weight: 600; font-size: .8rem; }
    .live i { width: 8px; height: 8px; border-radius: 50%; background: var(--error); }
    .unassigned { display: inline-block; margin-left: 6px; padding: 1px 8px; border-radius: 999px; font-size: .7rem; font-weight: 600;
      color: var(--warning-text); border: 1px solid currentColor; }
    .no-streams { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; margin-bottom: 16px; padding: 12px 16px;
      border: 1px solid var(--border); border-left: 3px solid var(--warning); border-radius: 12px; background: var(--surface); }
    .no-streams span { flex: 1; min-width: 240px; font-size: .875rem; }
    .no-streams mat-icon { color: var(--warning); }
  `,
  template: `
    <app-page-header eyebrow="Operations" title="Stream Operators"
      subtitle="Accounts that can only broadcast and record the streams you assign. They sign in at this portal and see the Operator console only." />

    @if (data(); as d) {
      <div class="stats">
        <app-stat-card label="Operators" icon="video_camera_front" [value]="d.operators.length" [hint]="activeCount() + ' active'" />
        <app-stat-card label="Live sessions" icon="sensors" [tone]="d.active_sessions.length ? 'success' : 'neutral'" [value]="d.active_sessions.length" />
        <app-stat-card label="Recording now" icon="fiber_manual_record" [value]="recordingNow()" />
        <app-stat-card label="Streams" icon="live_tv" [value]="d.streams.length" hint="Manage under Live streaming" />
      </div>
      @if (!d.streams.length) {
        <div class="no-streams" role="status">
          <mat-icon>live_tv</mat-icon>
          <span><strong>This workspace has no stream yet</strong>, so there is nothing to assign. Create the arena stream
            (visible to players, default for every event), then pick it in each Operator’s <em>Streams</em> column.</span>
          <button mat-flat-button type="button" (click)="createDefaultStream()" [disabled]="busy()"><mat-icon>add</mat-icon>Create arena stream</button>
          <a mat-button routerLink="/streams">Live streaming settings</a>
        </div>
      }
    }

    <section class="panel" aria-labelledby="new-op">
      <h2 id="new-op">Create an Operator</h2>
      <p class="muted">Leave the password empty to generate a temporary one that must be changed at first sign-in.</p>
      <form class="form" [formGroup]="form" (ngSubmit)="create()">
        <mat-form-field><mat-label>Username</mat-label><input matInput formControlName="username" autocomplete="off" />
          <mat-error><app-field-error [control]="form.controls.username" /></mat-error></mat-form-field>
        <mat-form-field><mat-label>Full name</mat-label><input matInput formControlName="full_name" autocomplete="off" />
          <mat-error><app-field-error [control]="form.controls.full_name" /></mat-error></mat-form-field>
        <mat-form-field><mat-label>Password (optional)</mat-label><input matInput formControlName="password" type="password" autocomplete="new-password" /></mat-form-field>
        <mat-form-field>
          <mat-label>Streams</mat-label>
          <mat-select formControlName="stream_ids" multiple>
            @for (s of data()?.streams ?? []; track s.id) { <mat-option [value]="s.id">{{ s.name }}</mat-option> }
          </mat-select>
          @if (data() && !data()!.streams.length) { <mat-hint>No streams yet: create one first</mat-hint> }
        </mat-form-field>
        <div class="submit"><button mat-flat-button type="submit" [disabled]="busy() || form.invalid">Create Operator</button></div>
      </form>
    </section>

    <section class="panel" aria-labelledby="ops-h">
      <h2 id="ops-h">Operators</h2>
      @if (data(); as d) {
        @if (!d.operators.length) {
          <app-empty-state icon="video_camera_front" title="No Operators yet" message="Create one above, then assign the streams they run." />
        } @else {
          <div class="table-wrap">
            <table>
              <thead><tr><th scope="col">Operator</th><th scope="col">Status</th><th scope="col">Streams</th><th scope="col">Now</th><th scope="col">Last sign-in</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
              <tbody>
                @for (o of d.operators; track o.id) {
                  <tr>
                    <td class="who"><strong>{{ o.full_name }}</strong><span>{{ o.username }}@if (o.must_change_password) { · temporary password }</span></td>
                    <td><app-status-chip [status]="o.status" />
                      @if (!o.stream_ids.length) { <span class="unassigned" matTooltip="Their console cannot stream until a stream is assigned">No stream</span> }</td>
                    <td>
                      <mat-form-field class="assign" subscriptSizing="dynamic">
                        <mat-label>Assigned streams</mat-label>
                        <mat-select multiple [value]="o.stream_ids" (selectionChange)="assign(o, $event.value)" [aria-label]="'Streams for ' + o.username"
                          [disabled]="!d.streams.length" [placeholder]="d.streams.length ? '' : 'No streams yet'">
                          @for (s of d.streams; track s.id) { <mat-option [value]="s.id">{{ s.name }}</mat-option> }
                        </mat-select>
                      </mat-form-field>
                    </td>
                    <td>
                      @if (o.broadcast; as b) { <span class="live"><i></i>{{ b.status === 'live' ? 'Live' : 'Connecting' }} · {{ b.stream_name }}</span> }
                      @else { <span class="muted">—</span> }
                    </td>
                    <td>{{ o.last_login_at | relativeTime }}</td>
                    <td>
                      <div class="row-actions">
                        @if (o.status === 'active') {
                          <button mat-button type="button" (click)="setActive(o, false)">Deactivate</button>
                        } @else if (o.status === 'suspended') {
                          <button mat-button type="button" (click)="setActive(o, true)">Activate</button>
                        }
                        <button mat-button type="button" (click)="reset(o)">Reset password</button>
                        <a mat-button [routerLink]="['/users', o.id]">Account</a>
                      </div>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
      } @else { <p class="muted" role="status">Loading…</p> }
    </section>

    <section class="panel" aria-labelledby="sessions-h">
      <h2 id="sessions-h">Active streaming sessions</h2>
      @if (data()?.active_sessions?.length) {
        <div class="table-wrap">
          <table>
            <thead><tr><th scope="col">Stream</th><th scope="col">Source</th><th scope="col">Operator</th><th scope="col">Status</th><th scope="col">Started</th></tr></thead>
            <tbody>
              @for (b of data()!.active_sessions; track b.id) {
                <tr>
                  <td>{{ b.stream_name }}</td>
                  <td>{{ method[b.method] }}@if (b.resolution) { · {{ b.resolution }} }</td>
                  <td>{{ b.operator_name ?? '—' }}</td>
                  <td><app-status-chip [status]="b.status" /></td>
                  <td>{{ b.started_at | date: 'medium' }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      } @else { <p class="muted">Nothing is streaming right now.</p> }
      @if (data()?.active_recordings?.length) {
        <p class="muted" style="margin-top: 12px">Recording: @for (r of data()!.active_recordings; track r.id) { {{ r.stream_name }} ({{ r.started_by_name ?? 'automatic' }}, since {{ r.started_at | date: 'shortTime' }}){{ $last ? '' : ', ' }} }</p>
      }
    </section>

    @if (data()?.recent_sessions?.length) {
      <section class="panel" aria-labelledby="recent-h">
        <h2 id="recent-h">Recent sessions</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th scope="col">Stream</th><th scope="col">Source</th><th scope="col">Operator</th><th scope="col">Started</th><th scope="col">Ended</th><th scope="col">Reason</th></tr></thead>
            <tbody>
              @for (b of data()!.recent_sessions; track b.id) {
                <tr>
                  <td>{{ b.stream_name }}</td>
                  <td>{{ method[b.method] }}</td>
                  <td>{{ b.operator_name ?? '—' }}</td>
                  <td>{{ b.started_at | date: 'short' }}</td>
                  <td>{{ b.ended_at | date: 'short' }}</td>
                  <td class="muted">{{ b.end_reason }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>
    }
  `,
})
export class OperatorsPage {
  private readonly admin = inject(AdminApi);
  private readonly ops = inject(OperatorApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly dialog = inject(MatDialog);
  readonly method = METHOD;
  readonly data = signal<OperatorsOverview | null>(null);
  readonly busy = signal(false);
  readonly activeCount = computed(() => this.data()?.operators.filter((o) => o.status === 'active').length ?? 0);
  readonly recordingNow = computed(() => this.data()?.active_recordings.filter((r) => r.status === 'recording').length ?? 0);

  readonly form = inject(FormBuilder).nonNullable.group({
    username: ['', [Validators.required, Validators.minLength(3), Validators.maxLength(64), Validators.pattern(/^[A-Za-z0-9_.-]+$/)]],
    full_name: ['', [Validators.required, Validators.maxLength(200)]],
    password: '',
    stream_ids: [[] as string[]],
  });

  constructor() {
    void this.load();
    interval(10000).pipe(takeUntilDestroyed()).subscribe(() => void this.load());
    inject(RealtimeService).on('video.broadcast', 'video.recording', 'video.stream_status').pipe(takeUntilDestroyed()).subscribe(() => void this.load());
  }

  async load(): Promise<void> {
    try {
      const data = await firstValueFrom(this.ops.operators());
      this.data.set(data);
      // One stream in the workspace: a new Operator gets it unless the admin clears it.
      const ids = this.form.controls.stream_ids;
      if (data.streams.length === 1 && !ids.value.length && !ids.dirty) ids.setValue([data.streams[0].id]);
    } catch (err) {
      if (!this.data()) this.toast.error(err);
    }
  }

  async create(): Promise<void> {
    const v = this.form.getRawValue();
    this.busy.set(true);
    try {
      const created = await firstValueFrom(
        this.admin.createUser({ username: v.username, full_name: v.full_name, account_type: 'stream_operator', password: v.password || undefined }),
      );
      if (v.stream_ids.length) await firstValueFrom(this.ops.assignStreams(created.user.id, v.stream_ids));
      this.form.reset({ username: '', full_name: '', password: '', stream_ids: [] });
      if (!v.stream_ids.length) this.toast.info('No stream assigned yet: pick one in the Streams column so they can go live');
      this.toast.success(`Operator “${created.user.username}” created`);
      if (created.temporary_password) {
        this.dialog.open(TempPasswordDialog, { data: created.temporary_password, width: '480px', maxWidth: '95vw' });
      }
      await this.load();
    } catch (err) {
      this.toast.error(toApiError(err));
    } finally {
      this.busy.set(false);
    }
  }

  /** One click: the arena stream players watch on every event (browser camera/screen or OBS). */
  async createDefaultStream(): Promise<void> {
    this.busy.set(true);
    try {
      const s = await firstValueFrom(this.admin.createStream({ name: 'Arena Camera', source_kind: 'obs', is_default: true, enabled: true }));
      this.toast.success(`Stream “${s.name}” created and visible to players. Assign it in the Streams column.`);
      await this.load();
    } catch (err) {
      this.toast.error(toApiError(err));
    } finally {
      this.busy.set(false);
    }
  }

  async assign(o: OperatorAccount, streamIds: string[]): Promise<void> {
    try {
      await firstValueFrom(this.ops.assignStreams(o.id, streamIds));
      this.toast.success(`Streams updated for ${o.username}`);
    } catch (err) {
      this.toast.error(toApiError(err));
    }
    await this.load();
  }

  async setActive(o: OperatorAccount, active: boolean): Promise<void> {
    if (!active) {
      const ok = await this.confirm.ask({
        title: `Deactivate ${o.username}?`,
        message: 'They are signed out everywhere and any broadcast they are running is disconnected.',
        confirmText: 'Deactivate',
        destructive: true,
      });
      if (ok === null) return;
    }
    try {
      await firstValueFrom(active ? this.admin.activateUser(o.id) : this.admin.suspendUser(o.id));
      this.toast.success(active ? `${o.username} can sign in again` : `${o.username} deactivated`);
    } catch (err) {
      this.toast.error(toApiError(err));
    }
    await this.load();
  }

  async reset(o: OperatorAccount): Promise<void> {
    const ok = await this.confirm.ask({
      title: `Reset the password of ${o.username}?`,
      message: 'They are signed out everywhere (a running broadcast is disconnected) and must choose a new password.',
      confirmText: 'Reset password',
    });
    if (ok === null) return;
    try {
      const r = await firstValueFrom(this.admin.resetPassword(o.id));
      this.dialog.open(TempPasswordDialog, { data: r.temporary_password, width: '480px', maxWidth: '95vw' });
    } catch (err) {
      this.toast.error(toApiError(err));
    }
    await this.load();
  }
}

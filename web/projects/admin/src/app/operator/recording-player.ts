import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { firstValueFrom } from 'rxjs';

import type { Recording, RecordingAccess } from '@shared/models';
import { OperatorApi, toApiError } from '@shared/services';

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i < 2 ? 0 : 1)} ${units[i]}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—';
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

/** Plays and downloads one recording through short-lived signed links (refreshed on demand). */
@Component({
  selector: 'app-recording-player',
  imports: [DatePipe, MatDialogModule, MatButtonModule, MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    video { width: 100%; max-height: 62vh; background: var(--media-background); border-radius: 10px; display: block; }
    .parts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .meta { color: var(--text-secondary); font-size: .85rem; margin: 0 0 12px; }
    .error { color: var(--error-text); }
  `,
  template: `
    <h2 mat-dialog-title>{{ data.stream_name }} · {{ data.started_at | date: 'medium' }}</h2>
    <mat-dialog-content>
      <p class="meta">{{ duration(data.duration_seconds) }} · {{ bytes(data.size_bytes) }} · kept until {{ data.expires_at | date: 'mediumDate' }}</p>
      @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
      @if (access(); as a) {
        @if (current(); as file) {
          <video controls autoplay playsinline preload="metadata" [src]="file.play_url" (error)="expired()"
            [attr.aria-label]="'Recording of ' + data.stream_name"></video>
        }
        <div class="parts">
          @for (f of a.files; track f.index) {
            @if (a.files.length > 1) {
              <button mat-stroked-button type="button" [disabled]="f.index === index()" (click)="index.set(f.index)">
                Part {{ f.index + 1 }} · {{ duration(f.duration) }}
              </button>
            }
            <a mat-button [href]="f.download_url" download><mat-icon>download</mat-icon>Download{{ a.files.length > 1 ? ' part ' + (f.index + 1) : '' }} ({{ bytes(f.size) }})</a>
          }
        </div>
      } @else if (!error()) {
        <p class="meta" role="status">Preparing playback…</p>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end"><button mat-flat-button type="button" mat-dialog-close>Close</button></mat-dialog-actions>
  `,
})
export class RecordingPlayerDialog {
  readonly data = inject<Recording>(MAT_DIALOG_DATA);
  private readonly api = inject(OperatorApi);
  readonly access = signal<RecordingAccess | null>(null);
  readonly error = signal<string | null>(null);
  readonly index = signal(0);
  readonly current = computed(() => this.access()?.files[this.index()] ?? null);
  readonly bytes = formatBytes;
  readonly duration = formatDuration;
  private refreshed = false;

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      this.access.set(await firstValueFrom(this.api.recordingAccess(this.data.id)));
      this.error.set(null);
    } catch (err) {
      this.error.set(toApiError(err).message);
    }
  }

  /** Links last 15 minutes; fetch fresh ones once if playback fails. */
  expired(): void {
    if (this.refreshed) {
      this.error.set('This recording could not be played in this browser. Download it instead.');
      return;
    }
    this.refreshed = true;
    void this.load();
  }
}

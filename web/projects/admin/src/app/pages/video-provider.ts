import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatSelectModule } from '@angular/material/select';
import { ApiClient } from '@shared/services';

@Component({
  selector: 'app-video-provider-settings',
  imports: [
    ReactiveFormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatCheckboxModule,
    MatSelectModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [
    `
      /* Same spacing and heading scale as the other settings tabs. */
      :host { display: block; max-width: 64rem; padding: 16px 0; }
      h2 { margin: 0 0 4px; font-size: 1rem; font-weight: 600; }
      h2 + p { margin: 0 0 12px; color: var(--text-secondary); font-size: 0.875rem; line-height: 1.6; }
      .fields {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 16px;
        margin: 16px 0;
      }
      .actions {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
      }
    `,
  ],
  template: `
    <h2>External Video Provider</h2>
    <p>Configure an authorized source for the existing Player and Declarator video containers.</p>
    <form [formGroup]="form" (ngSubmit)="save()">
      <mat-checkbox formControlName="enabled">Enable provider</mat-checkbox>
      <div class="fields">
        <mat-form-field
          ><mat-label>Provider adapter</mat-label
          ><mat-select formControlName="adapter"
            >@if (demoAvailable() || form.controls.adapter.value === 'demo') {<mat-option value="demo" [disabled]="!demoAvailable()"
              >Licensed demo{{ demoAvailable() ? '' : ' (development only)' }}</mat-option
            >}<mat-option value="json-bearer-v1">Authorized JSON bearer v1</mat-option></mat-select
          ></mat-form-field
        >
        @for (field of fields; track field.key) {
          <mat-form-field
            ><mat-label>{{ field.label }}</mat-label
            ><input
              matInput
              [formControlName]="field.key"
              [type]="field.key === 'password' ? 'password' : 'text'"
              [attr.autocomplete]="field.key === 'password' ? 'new-password' : 'off'"
          /></mat-form-field>
        }
      </div>
      <p>
        Credentials saved: {{ credentialsSaved() ? 'Yes' : 'No' }}. Leave both credential fields
        blank to retain them; supply both to replace them.
      </p>
      <mat-checkbox formControlName="clear_credentials">Delete saved credentials</mat-checkbox>
      <p>Broadcasting and WebSocket fields are reserved for a documented provider adapter.</p>
      <div class="actions">
        <button mat-flat-button type="submit" [disabled]="busy()">Save Configuration</button>
        @for (action of actions; track action.key) {
          <button mat-stroked-button type="button" [disabled]="busy()" (click)="run(action.key)">
            {{ action.label }}
          </button>
        }
        <button mat-stroked-button type="button" [disabled]="busy()" (click)="disable()">
          Disable Provider
        </button>
      </div>
    </form>
    <p role="status" aria-live="polite">{{ busy() ? 'Connecting to video source…' : message() }}</p>
  `,
})
export class VideoProviderSettings {
  private readonly api = inject(ApiClient);
  private readonly destroyRef = inject(DestroyRef);
  readonly busy = signal(false);
  readonly message = signal('');
  readonly credentialsSaved = signal(false);
  readonly demoAvailable = signal(true);
  readonly fields = [
    { key: 'website_url', label: 'External Website URL' },
    { key: 'api_base_url', label: 'API Base URL' },
    { key: 'login_endpoint', label: 'Login API Endpoint' },
    { key: 'broadcasting_auth_endpoint', label: 'Broadcasting Auth Endpoint' },
    { key: 'playback_endpoint', label: 'Video Playback API Endpoint' },
    { key: 'live_event_endpoint', label: 'Live Event API Endpoint' },
    { key: 'websocket_url', label: 'WebSocket URL (if supported)' },
    { key: 'username', label: 'Website Username' },
    { key: 'password', label: 'Website Password' },
    { key: 'game_id', label: 'External Game ID' },
    { key: 'video_id', label: 'External Video ID' },
    { key: 'event_id', label: 'Assigned Internal Event ID' },
  ];
  readonly actions = [
    { key: 'test', label: 'Test Connection' },
    { key: 'playback', label: 'Fetch Video' },
    { key: 'refresh', label: 'Refresh / Refetch' },
    { key: 'reconnect', label: 'Reconnect' },
  ];
  readonly form = inject(FormBuilder).nonNullable.group({
    enabled: false,
    adapter: 'demo',
    website_url: '',
    api_base_url: '',
    login_endpoint: '/api/auth/login',
    broadcasting_auth_endpoint: '/broadcasting/auth',
    playback_endpoint: '/api/video/get-playback-url',
    live_event_endpoint: '/api/games/live/fetch',
    websocket_url: '',
    username: '',
    password: '',
    game_id: '',
    video_id: '',
    event_id: '',
    clear_credentials: false,
  });
  private readonly path = '/admin/settings/video-provider';
  constructor() {
    this.api
      .get<Record<string, unknown>>(this.path)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (data) => {
          this.form.patchValue(data);
          this.form.controls.event_id.setValue(String(data['event_id'] ?? ''));
          this.credentialsSaved.set(!!data['credentials_saved']);
          this.demoAvailable.set(data['demo_available'] !== false);
          this.message.set(String(data['status']));
        },
        error: () => this.message.set('Provider configuration could not be loaded'),
      });
  }
  save(): void {
    if (this.busy()) return;
    const value = this.form.getRawValue();
    const body: Record<string, unknown> = { ...value, event_id: value.event_id || null };
    if (!value.username && !value.password) {
      delete body['username'];
      delete body['password'];
    }
    this.busy.set(true);
    this.api
      .put<Record<string, unknown>>(this.path, body)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (data) => {
          this.busy.set(false);
          this.credentialsSaved.set(!!data['credentials_saved']);
          this.demoAvailable.set(data['demo_available'] !== false);
          this.form.patchValue({ username: '', password: '', clear_credentials: false });
          this.message.set('Configuration saved');
        },
        error: (err) => {
          this.busy.set(false);
          this.message.set(err.message || 'Configuration could not be saved');
        },
      });
  }
  disable(): void {
    this.form.controls.enabled.setValue(false);
    this.save();
  }
  run(action: string): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.api
      .post<{ message: string; status: string }>(`${this.path}/${action}`)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.busy.set(false);
          this.message.set(`${result.message} (${result.status})`);
        },
        error: () => {
          this.busy.set(false);
          this.message.set('Provider action failed');
        },
      });
  }
}

import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { MatTooltipModule } from '@angular/material/tooltip';

import { RealtimeService } from '@shared/services';
import { PlayerPrefs } from '../core/player-prefs';

/** Live-update connection state; activating it reconnects. */
@Component({
  selector: 'pp-connection-status',
  imports: [MatTooltipModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    button { display: inline-flex; align-items: center; gap: 0.45rem; border: 1px solid var(--pp-rope); background: transparent;
      color: var(--pp-ash); border-radius: 999px; padding: 0.3rem 0.7rem; font: 500 0.78rem/1 var(--pp-text); cursor: pointer; }
    button:hover { color: var(--pp-chalk); border-color: var(--control-border); }
    .dot { width: 0.5rem; height: 0.5rem; border-radius: 999px; background: var(--pp-void); }
    .open .dot { background: var(--pp-draw); box-shadow: 0 0 0 3px color-mix(in srgb, var(--pp-draw) 22%, transparent); }
    .connecting .dot { background: var(--pp-lamp); animation: pp-pulse 1.2s ease-in-out infinite; }
    .compact { padding: 0.3rem; border: 0; }
  `,
  template: `
    <button type="button" [class]="state()" [class.compact]="compact()" (click)="reconnect()" [matTooltip]="tooltip()" [attr.aria-label]="tooltip()">
      <span class="dot" aria-hidden="true"></span>
      @if (!compact()) { <span>{{ label() }}</span> }
    </button>
  `,
})
export class ConnectionStatusComponent {
  readonly compact = input(false);
  private readonly realtime = inject(RealtimeService);
  private readonly prefs = inject(PlayerPrefs);

  readonly state = computed(() => (this.prefs.liveUpdates() ? this.realtime.state() : 'idle'));
  readonly label = computed(() => {
    switch (this.state()) {
      case 'open':
        return 'Live';
      case 'connecting':
        return 'Connecting';
      default:
        return this.prefs.liveUpdates() ? 'Offline' : 'Live updates off';
    }
  });
  readonly tooltip = computed(() =>
    this.state() === 'open' ? 'Receiving live results' : this.prefs.liveUpdates() ? 'Not connected. Select to reconnect.' : 'Turn on live updates in Settings',
  );

  reconnect(): void {
    if (!this.prefs.liveUpdates() || this.realtime.state() === 'open') return;
    this.realtime.disconnect();
    this.realtime.connect();
  }
}

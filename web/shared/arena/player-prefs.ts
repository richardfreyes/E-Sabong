import { DOCUMENT } from '@angular/common';
import { Injectable, computed, effect, inject, signal } from '@angular/core';

export interface PlayerPreferences {
  /** Keep a WebSocket open for live scoreboard, results and points. */
  liveUpdates: boolean;
  /** Show the full-width banner when an official result is declared. */
  announcements: boolean;
  /** Cut decorative motion regardless of the OS setting. */
  reduceMotion: boolean;
  /** Desktop side navigation shows icons only. */
  railCollapsed: boolean;
  /** Start the video muted (browsers require this for autoplay). */
  videoMuted: boolean;
}

const KEY = 'esb.player.prefs';
const DEFAULTS: PlayerPreferences = {
  liveUpdates: true,
  announcements: true,
  reduceMotion: false,
  railCollapsed: false,
  videoMuted: true,
};

/** Per-device display preferences for the player portal (not account settings). */
@Injectable({ providedIn: 'root' })
export class PlayerPrefs {
  private readonly doc = inject(DOCUMENT);
  private readonly state = signal<PlayerPreferences>(this.read());

  readonly value = this.state.asReadonly();
  readonly liveUpdates = computed(() => this.state().liveUpdates);
  readonly announcements = computed(() => this.state().announcements);
  readonly reduceMotion = computed(() => this.state().reduceMotion);
  readonly railCollapsed = computed(() => this.state().railCollapsed);
  readonly videoMuted = computed(() => this.state().videoMuted);

  constructor() {
    effect(() => this.doc.documentElement.classList.toggle('pp-reduce-motion', this.reduceMotion()));
  }

  update(patch: Partial<PlayerPreferences>): void {
    this.state.update((s) => ({ ...s, ...patch }));
    try {
      localStorage.setItem(KEY, JSON.stringify(this.state()));
    } catch {
      /* storage unavailable: preferences last for this visit */
    }
  }

  private read(): PlayerPreferences {
    try {
      const raw = localStorage.getItem(KEY);
      return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<PlayerPreferences>) } : DEFAULTS;
    } catch {
      return DEFAULTS;
    }
  }
}

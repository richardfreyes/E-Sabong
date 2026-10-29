import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal, untracked } from '@angular/core';
import { PointsPipe } from '@shared/pipes';
import { PICK_AMOUNTS } from '../core/player-picks';

/** A non-redeemable demonstration amount. Selection never changes the balance. */
@Component({
  selector: 'pp-amount-picker',
  imports: [PointsPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; }
    .field { display: flex; align-items: center; border: 1px solid var(--pp-rope); border-radius: 0.6rem; background: var(--pp-graphite); }
    .field:focus-within { border-color: var(--mat-sys-primary); box-shadow: 0 0 0 2px color-mix(in srgb, var(--mat-sys-primary) 15%, transparent); }
    .field.bad { border-color: var(--pp-meron); }
    input { width: 100%; min-width: 0; padding: 0.65rem 0.85rem; border: 0; outline: 0; background: transparent;
      color: var(--pp-chalk); font: 600 1.15rem var(--pp-text); font-variant-numeric: tabular-nums; }
    .unit { padding-right: 0.85rem; color: var(--pp-ash); font-size: 0.75rem; }
    .quick { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.45rem; margin-top: 0.65rem; }
    button { min-height: var(--control-height); padding: 0.45rem 0.2rem; border: 1px solid var(--pp-rope); border-radius: 0.5rem;
      background: var(--pp-pit-raised); color: var(--pp-chalk); font: 600 0.8rem var(--pp-text); cursor: pointer;
      transition: background 160ms ease, border-color 160ms ease; }
    button:hover:not(:disabled) { border-color: var(--mat-sys-primary); }
    button.on { border-color: var(--mat-sys-primary); color: var(--mat-sys-primary); background: color-mix(in srgb, var(--mat-sys-primary) 10%, var(--pp-pit)); }
    button:focus-visible { outline: 2px solid var(--pp-chalk); outline-offset: 2px; }
    button:disabled { opacity: 0.35; cursor: not-allowed; }
    .summary { display: flex; justify-content: space-between; gap: 0.5rem; margin: 0.8rem 0 0; color: var(--pp-ash); font-size: 0.7rem; }
    .summary b { color: var(--pp-chalk); font-weight: 600; font-variant-numeric: tabular-nums; }
    .available b { color: var(--pp-lamp); }
    .error { margin: 0.4rem 0 0; color: var(--error-text); font-size: 0.75rem; }
    .error:empty { display: none; }
  `,
  template: `
    <div class="field" [class.bad]="!!error()">
      <input id="pp-amount" aria-label="Amount in demo points" type="number" inputmode="numeric" min="1" [max]="available() ?? undefined" step="1"
             [value]="draft()" [attr.aria-invalid]="!!error()" aria-describedby="pp-amount-error pp-amount-summary"
             (input)="type($event)" (blur)="commit()" (keydown.enter)="commit()" />
      <span class="unit">pts</span>
    </div>
    <p id="pp-amount-error" class="error" role="alert">{{ error() }}</p>
    <div class="quick" aria-label="Quick amounts">
      @for (value of quickValues; track value) {
        <button type="button" [class.on]="amount() === value" [disabled]="tooMuch(value)" (click)="set(value)"
                [attr.aria-label]="(value | points) + ' demo points'" [attr.aria-pressed]="amount() === value">{{ value | points }}</button>
      }
    </div>
    <p id="pp-amount-summary" class="summary" aria-live="polite">
      <span>Selected <b>{{ amount() | points }}</b> pts</span>
      <span class="available">Available <b>{{ available() === null ? '—' : (available() | points) }}</b> pts</span>
    </p>
  `,
})
export class AmountPickerComponent {
  readonly amount = input.required<number>();
  readonly available = input<number | null>(null);
  readonly amountChange = output<number>();
  readonly quickValues = PICK_AMOUNTS;
  readonly draft = signal('');
  private readonly touched = signal(false);

  readonly error = computed(() => {
    if (!this.touched()) return '';
    const raw = this.draft().trim();
    if (!raw) return 'Enter an amount.';
    const value = Number(raw);
    if (!Number.isSafeInteger(value)) return 'Enter a whole number of points.';
    if (value < 1) return 'Enter at least 1 point.';
    const max = this.available();
    if (max !== null && value > max) return `Available balance: ${max.toLocaleString()} demo points.`;
    return '';
  });

  constructor() {
    effect(() => {
      const amount = this.amount();
      untracked(() => {
        if (!this.touched()) this.draft.set(String(amount));
      });
    });
  }

  tooMuch(value: number): boolean {
    const max = this.available();
    return max !== null && value > max;
  }

  set(value: number): void {
    if (this.tooMuch(value)) return;
    this.touched.set(false);
    this.draft.set(String(value));
    this.amountChange.emit(value);
  }

  type(event: Event): void {
    this.touched.set(true);
    this.draft.set((event.target as HTMLInputElement).value);
    if (!this.error()) this.amountChange.emit(Number(this.draft()));
  }

  commit(): void {
    if (this.error()) return;
    this.touched.set(false);
  }
}

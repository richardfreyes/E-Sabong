import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

export interface BarDatum {
  label: string;
  value: number;
}

/**
 * Single-series column chart (one measure over time). Bars use the tone-mapped
 * --mat-sys-primary, so contrast holds for any tenant colour and in dark mode.
 * A visually hidden table carries the same data for assistive technology.
 */
@Component({
  selector: 'app-bar-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; }
    .bar { fill: var(--chart-color, var(--mat-sys-primary)); transition: opacity var(--duration-fast); }
    .bar.dim { opacity: 0.45; }
    .grid { stroke: var(--mat-sys-outline-variant); stroke-width: 1; }
    .tick { fill: var(--mat-sys-on-surface-variant); font-size: 11px; }
    .tip { pointer-events: none; }
  `,
  template: `
    <figure class="m-0">
      <figcaption class="mb-2 flex items-baseline justify-between gap-2">
        <span class="text-sm font-medium text-on-surface">{{ title() }}</span>
        <span class="text-xs text-on-surface-variant">Total {{ total().toLocaleString() }}</span>
      </figcaption>
      <div class="relative" (mouseleave)="hover.set(null)">
        <svg [attr.viewBox]="'0 0 ' + width + ' ' + height" class="block h-48 w-full" [style.--chart-color]="color() === 'secondary' ? 'var(--mat-sys-secondary)' : 'var(--mat-sys-primary)'" preserveAspectRatio="none" role="img"
             [attr.aria-label]="title() + ', ' + data().length + ' categories, total ' + total()">
          @for (t of ticks(); track t.value) {
            <line class="grid" [attr.x1]="padL" [attr.x2]="width" [attr.y1]="t.y" [attr.y2]="t.y" vector-effect="non-scaling-stroke" />
          }
          @for (b of bars(); track b.label; let i = $index) {
            <path class="bar" [class.dim]="hover() !== null && hover() !== i" [attr.d]="b.path" />
            <!-- Hit target spans the full column height, wider than the bar. -->
            <rect [attr.x]="b.slotX" y="0" [attr.width]="b.slotW" [attr.height]="height - padB" fill="transparent"
                  (mouseenter)="hover.set(i)" (focus)="hover.set(i)" tabindex="0" [attr.aria-label]="b.label + ': ' + b.value" />
          }
        </svg>
        <div class="pointer-events-none absolute inset-x-0 bottom-0 flex justify-between pl-8 text-[11px] text-on-surface-variant">
          <span [class.mx-auto]="data().length === 1">{{ data()[0]?.label }}</span>@if (data().length > 1) { <span>{{ data()[data().length - 1]?.label }}</span> }
        </div>
        <div class="pointer-events-none absolute left-0 top-0 flex h-[calc(100%-1.25rem)] flex-col justify-between text-[11px] text-on-surface-variant">
          <span>{{ max().toLocaleString() }}</span><span>0</span>
        </div>
        @if (bars()[hover() ?? -1]; as b) {
          <div class="tip absolute -translate-x-1/2 rounded-lg bg-inverse-surface px-2 py-1 text-xs shadow"
               style="background: var(--mat-sys-inverse-surface); color: var(--mat-sys-inverse-on-surface)"
               [style.left.%]="b.centerPct" [style.top.px]="0">
            <div class="font-medium">{{ b.label }}</div>
            <div class="tabular-nums">{{ b.value.toLocaleString() }} {{ unit() }}</div>
          </div>
        }
      </div>
      <table class="sr-only">
        <caption>{{ title() }}</caption>
        <thead><tr><th scope="col">Period</th><th scope="col">{{ unit() || 'Value' }}</th></tr></thead>
        <tbody>
          @for (d of data(); track d.label) {
            <tr><td>{{ d.label }}</td><td>{{ d.value }}</td></tr>
          }
        </tbody>
      </table>
    </figure>
  `,
})
export class BarChartComponent {
  readonly title = input.required<string>();
  readonly data = input.required<BarDatum[]>();
  readonly unit = input('');
  readonly color = input<'primary' | 'secondary'>('primary');

  readonly width = 600;
  readonly height = 200;
  readonly padL = 32;
  readonly padB = 20;
  readonly hover = signal<number | null>(null);

  readonly total = computed(() => this.data().reduce((s, d) => s + d.value, 0));
  readonly max = computed(() => {
    const m = Math.max(0, ...this.data().map((d) => d.value));
    if (m <= 4) return 4;
    const mag = 10 ** Math.floor(Math.log10(m));
    return Math.ceil(m / mag) * mag;
  });

  readonly ticks = computed(() => {
    const plotH = this.height - this.padB;
    return [0, 0.5, 1].map((f) => ({ value: f, y: plotH - f * plotH + 0.5 }));
  });

  readonly bars = computed(() => {
    const data = this.data();
    const n = Math.max(data.length, 1);
    const plotW = this.width - this.padL;
    const plotH = this.height - this.padB;
    const slot = plotW / n;
    const gap = 2;
    const w = Math.max(2, Math.min(28, slot - gap));
    const r = Math.min(4, w / 2);
    return data.map((d, i) => {
      const x = this.padL + i * slot + (slot - w) / 2;
      const h = d.value > 0 ? Math.max(r, (d.value / this.max()) * plotH) : 0;
      const y = plotH - h;
      // Rounded data end at the top, square at the baseline.
      const path = h === 0 ? '' : `M${x},${plotH} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${plotH} Z`;
      return {
        label: d.label,
        value: d.value,
        path,
        slotX: this.padL + i * slot,
        slotW: slot,
        centerPct: ((this.padL + i * slot + slot / 2) / this.width) * 100,
      };
    });
  });
}

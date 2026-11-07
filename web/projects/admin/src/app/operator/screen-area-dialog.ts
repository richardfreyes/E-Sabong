import { ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';

import { type CropRect, MIN_CROP, clampCrop, isFullFrame, pixelRect } from './screen-crop';

export interface ScreenAreaData {
  /** The shared screen as captured (uncropped). */
  stream: MediaStream;
  area: CropRect | null;
}
/** ``null`` = broadcast the whole shared screen; undefined (dialog dismissed) = no change. */
export type ScreenAreaResult = CropRect | null;

type Drag = { mode: 'draw' | 'move' | 'nw' | 'ne' | 'sw' | 'se'; startX: number; startY: number; start: CropRect };

/**
 * Pick the part of the shared screen to broadcast: drag on the picture to draw an area, drag the
 * area to move it, drag a corner to resize it. Arrow keys move it (Shift: resize) for precision.
 */
@Component({
  selector: 'app-screen-area-dialog',
  imports: [MatDialogModule, MatButtonModule, MatIconModule, MatSlideToggleModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .stage { position: relative; background: var(--media-background); border-radius: 10px; overflow: hidden; user-select: none;
      touch-action: none; cursor: crosshair; }
    video { display: block; width: 100%; max-height: 64vh; object-fit: contain; }
    .shade { position: absolute; background: rgba(2, 6, 23, .62); pointer-events: none; }
    .area { position: absolute; border: 2px solid var(--accent); box-shadow: 0 0 0 1px rgba(0,0,0,.6); cursor: move; outline: none; }
    .area:focus-visible { box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 45%, transparent); }
    .handle { position: absolute; width: 14px; height: 14px; background: var(--accent); border: 2px solid #0b0d12; border-radius: 3px; }
    .nw { left: -8px; top: -8px; cursor: nwse-resize; } .ne { right: -8px; top: -8px; cursor: nesw-resize; }
    .sw { left: -8px; bottom: -8px; cursor: nesw-resize; } .se { right: -8px; bottom: -8px; cursor: nwse-resize; }
    .size { position: absolute; left: 6px; top: 6px; padding: 2px 8px; border-radius: 999px; font-size: .72rem; font-weight: 700;
      background: rgba(2, 6, 23, .85); color: #fff; white-space: nowrap; }
    .bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin-top: 12px; }
    .hint { margin: 0 0 10px; color: var(--text-secondary); font-size: .85rem; }
    .grow { flex: 1; }
  `,
  template: `
    <h2 mat-dialog-title>Select the area to broadcast</h2>
    <mat-dialog-content>
      <p class="hint">Drag on the picture to draw the area players will see. Drag inside it to move it, drag a corner to resize.
        Arrow keys move it; Shift + arrows resize.</p>
      <div class="stage" #stage (pointerdown)="down($event)" (pointermove)="move($event)" (pointerup)="up($event)" (pointercancel)="up($event)">
        <video #video autoplay muted playsinline (loadedmetadata)="measure()" aria-label="Shared screen"></video>
        @if (box(); as b) {
          <div class="shade" [style.left.px]="b.cx" [style.top.px]="b.cy" [style.width.px]="b.cw" [style.height.px]="b.ay - b.cy"></div>
          <div class="shade" [style.left.px]="b.cx" [style.top.px]="b.ay + b.ah" [style.width.px]="b.cw" [style.height.px]="b.cy + b.ch - b.ay - b.ah"></div>
          <div class="shade" [style.left.px]="b.cx" [style.top.px]="b.ay" [style.width.px]="b.ax - b.cx" [style.height.px]="b.ah"></div>
          <div class="shade" [style.left.px]="b.ax + b.aw" [style.top.px]="b.ay" [style.width.px]="b.cx + b.cw - b.ax - b.aw" [style.height.px]="b.ah"></div>
          <div class="area" tabindex="0" role="group" [attr.aria-label]="'Selected area ' + sizeLabel()" (keydown)="key($event)"
            [style.left.px]="b.ax" [style.top.px]="b.ay" [style.width.px]="b.aw" [style.height.px]="b.ah" data-drag="move">
            <span class="size">{{ sizeLabel() }}</span>
            @for (h of handles; track h) { <span class="handle {{ h }}" [attr.data-drag]="h"></span> }
          </div>
        }
      </div>
      <div class="bar">
        <mat-slide-toggle [checked]="lock()" (change)="setLock($event.checked)">Keep 16:9 (fills players’ video area)</mat-slide-toggle>
        <span class="grow"></span>
        <button mat-button type="button" (click)="center()">Center</button>
      </div>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" (click)="ref.close(null)"><mat-icon>fullscreen</mat-icon>Use whole screen</button>
      <button mat-button type="button" mat-dialog-close>Cancel</button>
      <button mat-flat-button type="button" (click)="ref.close(area())">Apply area</button>
    </mat-dialog-actions>
  `,
})
export class ScreenAreaDialog {
  readonly data = inject<ScreenAreaData>(MAT_DIALOG_DATA);
  readonly ref = inject(MatDialogRef<ScreenAreaDialog, ScreenAreaResult>);
  readonly handles = ['nw', 'ne', 'sw', 'se'] as const;
  private readonly stage = viewChild.required<ElementRef<HTMLDivElement>>('stage');
  private readonly video = viewChild.required<ElementRef<HTMLVideoElement>>('video');
  private drag: Drag | null = null;

  readonly area = signal<CropRect>(this.data.area && !isFullFrame(this.data.area) ? this.data.area : { x: 0.15, y: 0.15, w: 0.7, h: 0.7 });
  private readonly saved = !!this.data.area && !isFullFrame(this.data.area);
  /** New areas start 16:9; a saved area is shown exactly as it was. */
  readonly lock = signal(!this.saved);
  private measured = false;
  /** The picture's box inside the stage (object-fit: contain), in px. */
  readonly content = signal<{ x: number; y: number; w: number; h: number; vw: number; vh: number } | null>(null);

  readonly box = computed(() => {
    const c = this.content();
    if (!c) return null;
    const a = this.area();
    return { cx: c.x, cy: c.y, cw: c.w, ch: c.h, ax: c.x + a.x * c.w, ay: c.y + a.y * c.h, aw: a.w * c.w, ah: a.h * c.h };
  });
  readonly sizeLabel = computed(() => {
    const c = this.content();
    if (!c) return '';
    const r = pixelRect(this.area(), c.vw, c.vh);
    return `${r.width} × ${r.height}`;
  });

  constructor() {
    effect(() => {
      const el = this.video().nativeElement;
      if (el.srcObject !== this.data.stream) el.srcObject = this.data.stream;
    });
    const observer = new ResizeObserver(() => this.measure());
    effect((onCleanup) => {
      observer.observe(this.stage().nativeElement);
      onCleanup(() => observer.disconnect());
    });
  }

  measure(): void {
    const v = this.video().nativeElement;
    const s = this.stage().nativeElement.getBoundingClientRect();
    const r = v.getBoundingClientRect();
    if (!v.videoWidth || !v.videoHeight || !r.width) return;
    const scale = Math.min(r.width / v.videoWidth, r.height / v.videoHeight);
    const w = v.videoWidth * scale;
    const h = v.videoHeight * scale;
    this.content.set({ x: r.left - s.left + (r.width - w) / 2, y: r.top - s.top + (r.height - h) / 2, w, h, vw: v.videoWidth, vh: v.videoHeight });
    if (!this.measured) {
      this.measured = true;
      if (this.lock()) {
        this.area.set(this.fit(this.area(), 'se'));
        this.center();
      }
    }
  }

  setLock(on: boolean): void {
    this.lock.set(on);
    if (on) this.area.set(this.fit(this.area(), 'se'));
  }

  center(): void {
    const a = this.area();
    this.area.set(clampCrop({ ...a, x: (1 - a.w) / 2, y: (1 - a.h) / 2 }));
  }

  down(event: PointerEvent): void {
    const point = this.point(event);
    if (!point) return;
    const target = (event.target as HTMLElement).closest('[data-drag]') as HTMLElement | null;
    const mode = (target?.dataset['drag'] as Drag['mode'] | undefined) ?? 'draw';
    this.drag = { mode, startX: point.x, startY: point.y, start: this.area() };
    if (mode === 'draw') this.area.set({ x: point.x, y: point.y, w: MIN_CROP, h: MIN_CROP });
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  move(event: PointerEvent): void {
    const d = this.drag;
    const p = d && this.point(event);
    if (!d || !p) return;
    const dx = p.x - d.startX;
    const dy = p.y - d.startY;
    const s = d.start;
    let next: CropRect;
    switch (d.mode) {
      case 'move':
        next = { ...s, x: s.x + dx, y: s.y + dy };
        break;
      case 'draw':
        next = { x: Math.min(d.startX, p.x), y: Math.min(d.startY, p.y), w: Math.abs(dx), h: Math.abs(dy) };
        break;
      case 'nw':
        next = { x: s.x + dx, y: s.y + dy, w: s.w - dx, h: s.h - dy };
        break;
      case 'ne':
        next = { x: s.x, y: s.y + dy, w: s.w + dx, h: s.h - dy };
        break;
      case 'sw':
        next = { x: s.x + dx, y: s.y, w: s.w - dx, h: s.h + dy };
        break;
      default:
        next = { x: s.x, y: s.y, w: s.w + dx, h: s.h + dy };
    }
    this.area.set(d.mode === 'move' ? clampCrop(next) : this.fit(next, d.mode === 'draw' ? 'se' : d.mode));
  }

  up(event: PointerEvent): void {
    this.drag = null;
    (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
  }

  key(event: KeyboardEvent): void {
    const step = 0.01;
    const deltas: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const d = deltas[event.key];
    if (!d) return;
    event.preventDefault();
    const a = this.area();
    this.area.set(
      event.shiftKey ? this.fit({ ...a, w: a.w + d[0], h: a.h + d[1] }, 'se') : clampCrop({ ...a, x: a.x + d[0], y: a.y + d[1] }),
    );
  }

  /** Clamp to the picture and, when locked, to 16:9 in pixels (anchored at the corner opposite ``corner``). */
  private fit(c: CropRect, corner: 'nw' | 'ne' | 'sw' | 'se'): CropRect {
    let r = clampCrop(c);
    const content = this.content();
    if (this.lock() && content) {
      const ratio = (9 / 16) * (content.vw / content.vh); // height fraction per width fraction
      let w = r.w;
      let h = w * ratio;
      if (h > 1) { h = 1; w = h / ratio; }
      const right = r.x + r.w;
      const bottom = r.y + r.h;
      const x = corner === 'nw' || corner === 'sw' ? right - w : r.x;
      const y = corner === 'nw' || corner === 'ne' ? bottom - h : r.y;
      r = clampCrop({ x, y, w, h });
    }
    return r;
  }

  /** Pointer position as fractions of the picture (clamped). */
  private point(event: PointerEvent): { x: number; y: number } | null {
    const c = this.content();
    if (!c) return null;
    const s = this.stage().nativeElement.getBoundingClientRect();
    const x = (event.clientX - s.left - c.x) / c.w;
    const y = (event.clientY - s.top - c.y) / c.h;
    return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) };
  }
}

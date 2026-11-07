import { TestBed } from '@angular/core/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { Subject } from 'rxjs';

import { ScreenAreaDialog } from './screen-area-dialog';
import type { CropRect } from './screen-crop';

/** Layout is not computed in the test DOM: the picture is placed at 0,0 sized 1000×500 (a 1600×1000 screen). */
function open(area: CropRect | null) {
  const closed: unknown[] = [];
  TestBed.configureTestingModule({
    imports: [ScreenAreaDialog],
    providers: [
      { provide: MAT_DIALOG_DATA, useValue: { stream: new (class {})() as MediaStream, area } },
      { provide: MatDialogRef, useValue: { close: (v: unknown) => closed.push(v), afterOpened: () => new Subject<void>() } },
    ],
  });
  const fixture = TestBed.createComponent(ScreenAreaDialog);
  const dialog = fixture.componentInstance;
  dialog.content.set({ x: 0, y: 0, w: 1000, h: 500, vw: 1600, vh: 1000 });
  const stage = fixture.nativeElement.querySelector('.stage') as HTMLElement;
  stage.setPointerCapture = () => undefined;
  stage.releasePointerCapture = () => undefined;
  const pointer = (type: 'down' | 'move' | 'up', x: number, y: number, target: Element = stage) => {
    const event = { clientX: x, clientY: y, pointerId: 1, target, currentTarget: stage, preventDefault: () => undefined } as unknown as PointerEvent;
    dialog[type](event);
  };
  return { fixture, dialog, stage, pointer, closed };
}

const near = (a: CropRect | null, b: CropRect) => {
  expect(a).not.toBeNull();
  for (const k of ['x', 'y', 'w', 'h'] as const) expect(a![k]).toBeCloseTo(b[k], 3);
};

describe('Select area dialog', () => {
  beforeAll(() => {
    (globalThis as Record<string, unknown>)['ResizeObserver'] ??= class { observe() {} disconnect() {} };
  });

  it('starts with no area, so the first drag draws exactly what was dragged', () => {
    const { dialog, pointer } = open(null);
    expect(dialog.area()).toBeNull();
    dialog.setLock(false);
    pointer('down', 250, 125); // inside where an old default box used to be
    pointer('move', 750, 350);
    pointer('up', 750, 350);
    near(dialog.area(), { x: 0.25, y: 0.25, w: 0.5, h: 0.45 });
  });

  it('keeps 16:9 in screen pixels while drawing when locked', () => {
    const { dialog, pointer } = open(null);
    pointer('down', 250, 125);
    pointer('move', 750, 400);
    const a = dialog.area()!;
    expect((a.w * 1600) / (a.h * 1000)).toBeCloseTo(16 / 9, 2);
    expect(a.x).toBeCloseTo(0.25, 3);
    expect(a.y).toBeCloseTo(0.25, 3);
  });

  it('moves a saved area when dragged inside it, and draws a new one when dragged outside', () => {
    const saved = { x: 0.1, y: 0.1, w: 0.3, h: 0.3 };
    const { fixture, dialog, pointer } = open(saved);
    fixture.detectChanges();
    const inside = fixture.nativeElement.querySelector('.area') as HTMLElement;
    pointer('down', 200, 100, inside); // 0.2, 0.2: inside the saved area
    pointer('move', 500, 200, inside);
    near(dialog.area(), { x: 0.4, y: 0.3, w: 0.3, h: 0.3 });
    pointer('up', 500, 200, inside);
    dialog.setLock(false);
    pointer('down', 50, 400); // outside: new area
    pointer('move', 150, 475);
    near(dialog.area(), { x: 0.05, y: 0.8, w: 0.1, h: 0.15 });
  });

  it('cannot apply without an area; Clear removes it', () => {
    const { fixture, dialog, pointer } = open(null);
    fixture.detectChanges();
    const apply = () => [...fixture.nativeElement.querySelectorAll('button')].find((b: HTMLButtonElement) => b.textContent?.includes('Apply area')) as HTMLButtonElement;
    expect(apply().disabled).toBe(true);
    pointer('down', 100, 100);
    pointer('move', 400, 300);
    fixture.detectChanges();
    expect(apply().disabled).toBe(false);
    dialog.area.set(null);
    fixture.detectChanges();
    expect(apply().disabled).toBe(true);
  });
});

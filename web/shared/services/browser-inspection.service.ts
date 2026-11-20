import { DOCUMENT } from '@angular/common';
import { DestroyRef, Injectable, InjectionToken, NgZone, inject } from '@angular/core';

import { environment } from '../config/environment';

export const INSPECTION_DETERRENTS = new InjectionToken<boolean>('INSPECTION_DETERRENTS', {
  providedIn: 'root',
  factory: () => environment.inspectionDeterrents,
});

/** Best-effort browser affordance deterrents; never an authentication/security boundary. */
@Injectable({ providedIn: 'root' })
export class BrowserInspectionService {
  private readonly enabled = inject(INSPECTION_DETERRENTS);
  private readonly document = inject(DOCUMENT);
  private readonly zone = inject(NgZone);
  private readonly destroyRef = inject(DestroyRef);
  private started = false;

  start(): void {
    if (!this.enabled || this.started || !this.document.defaultView) return;
    this.started = true;
    // Bubble listeners respect controls that already handled an event. No propagation is
    // stopped, and ordinary key presses never trigger Angular change detection.
    this.zone.runOutsideAngular(() => {
      this.document.addEventListener('keydown', this.onKeydown);
      this.document.addEventListener('contextmenu', this.onContextMenu);
    });
    this.destroyRef.onDestroy(() => {
      this.document.removeEventListener('keydown', this.onKeydown);
      this.document.removeEventListener('contextmenu', this.onContextMenu);
    });
  }

  private readonly onKeydown = (event: KeyboardEvent): void => {
    if (!event.cancelable || event.defaultPrevented || event.isComposing || event.getModifierState('AltGraph')) return;
    const f12 = event.key === 'F12' && !event.ctrlKey && !event.shiftKey && !event.altKey && !event.metaKey;
    const windows = event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey;
    const mac = event.metaKey && event.altKey && !event.ctrlKey && !event.shiftKey;
    // event.code covers macOS Option-modified characters and non-US keyboard layouts.
    const inspectKey = ['KeyI', 'KeyJ', 'KeyC'].includes(event.code)
      || ['i', 'j', 'c'].includes(event.key.toLowerCase());
    if (f12 || ((windows || mac) && inspectKey)) event.preventDefault();
  };

  private readonly onContextMenu = (event: MouseEvent): void => {
    // Keep keyboard/assistive context menus, editing, selected-text actions and native
    // media controls available. Ctrl-click on macOS is also a pointer context menu.
    if (!event.cancelable || event.defaultPrevented || (event.button !== 2 && !(event.button === 0 && event.ctrlKey))) return;
    if (this.document.getSelection()?.isCollapsed === false) return;
    const editable = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="searchbox"], [role="combobox"], video, audio';
    if (event.composedPath().some(node => node instanceof Element && node.matches(editable))) return;
    event.preventDefault();
  };
}

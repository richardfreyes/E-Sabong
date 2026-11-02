import { DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { debounceTime } from 'rxjs';
import { RealtimeService } from './realtime.service';

/** Personal, authorized notifications invalidate reads; values always come from the API. */
export function watchPoints(refresh: () => void): void {
  inject(RealtimeService).on('points.updated', 'connected').pipe(
    debounceTime(150), takeUntilDestroyed(inject(DestroyRef)),
  ).subscribe(refresh);
}

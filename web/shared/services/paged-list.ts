import { DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Observable, Subject, catchError, of, switchMap, tap } from 'rxjs';

import type { Page, PageQuery } from '@shared/models';
import { toApiError } from './api-client';

/**
 * Signal-backed state for a paginated, searchable, sortable list. Stale
 * responses are discarded (switchMap), so fast typing never shows old results.
 * Must be created in an injection context.
 */
export class PagedList<T> {
  readonly rows = signal<T[]>([]);
  readonly total = signal(0);
  readonly page = signal(1);
  readonly pageSize = signal(25);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  private q: string | null = null;
  private sort: string | null = null;
  private filters: Record<string, unknown> = {};
  private readonly trigger = new Subject<void>();

  constructor(fetcher: (query: PageQuery) => Observable<Page<T>>, initialFilters: Record<string, unknown> = {}) {
    this.filters = { ...initialFilters };
    this.trigger
      .pipe(
        tap(() => {
          this.loading.set(true);
          this.error.set(null);
        }),
        switchMap(() =>
          fetcher({ page: this.page(), page_size: this.pageSize(), q: this.q, sort: this.sort, ...(this.filters as PageQuery) }).pipe(
            catchError((err: unknown) => {
              this.error.set(toApiError(err).message);
              return of(null);
            }),
          ),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((res) => {
        this.loading.set(false);
        if (!res) return;
        this.rows.set(res.items);
        this.total.set(res.total);
      });
  }

  load(): void {
    this.trigger.next();
  }

  setPage(e: { page: number; pageSize: number }): void {
    this.page.set(e.page);
    this.pageSize.set(e.pageSize);
    this.load();
  }

  setSearch(q: string): void {
    this.q = q || null;
    this.page.set(1);
    this.load();
  }

  setSort(sort: string | null): void {
    this.sort = sort;
    this.load();
  }

  setFilter(key: string, value: unknown): void {
    this.filters = { ...this.filters, [key]: value };
    this.page.set(1);
    this.load();
  }

  filter(key: string): unknown {
    return this.filters[key];
  }
}

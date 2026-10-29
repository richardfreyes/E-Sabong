import { NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  Directive,
  TemplateRef,
  computed,
  contentChildren,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatPaginatorModule, PageEvent } from '@angular/material/paginator';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSortModule, Sort } from '@angular/material/sort';
import { MatTableModule } from '@angular/material/table';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';

import { EmptyStateComponent, ErrorStateComponent, SkeletonComponent } from './primitives';

export interface Column<T> {
  key: string;
  label: string;
  sortable?: boolean;
  align?: 'start' | 'end';
  /** Hidden below the `sm` breakpoint to keep tables readable on phones. */
  hideOnMobile?: boolean;
  cell?: (row: T) => string | number | null | undefined;
}

/** `<ng-template appCell="status" let-row>` overrides how one column renders. */
@Directive({ selector: 'ng-template[appCell]' })
export class CellDefDirective {
  readonly name = input.required<string>({ alias: 'appCell' });
  readonly template = inject<TemplateRef<{ $implicit: unknown }>>(TemplateRef);
}

@Component({
  selector: 'app-data-table',
  imports: [
    NgTemplateOutlet,
    MatTableModule,
    MatSortModule,
    MatPaginatorModule,
    MatProgressBarModule,
    MatFormFieldModule,
    MatInputModule,
    MatIconModule, MatButtonModule,
    EmptyStateComponent,
    ErrorStateComponent,
    SkeletonComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './data-table.scss',
  template: `
    <div class="table-panel">
      @if (searchable() || hasFilters) {
        <div class="table-toolbar">
          @if (searchable()) {
            <mat-form-field appearance="outline" subscriptSizing="dynamic" class="dense search-field">
              <mat-icon matPrefix aria-hidden="true">search</mat-icon>
              <input matInput type="search" [placeholder]="searchPlaceholder()" [attr.aria-label]="searchPlaceholder()"
                     [value]="searchValue()" (input)="onSearch($any($event.target).value)" />
              @if (searchValue()) { <button mat-icon-button matSuffix type="button" aria-label="Clear search" (click)="onSearch('')"><mat-icon>close</mat-icon></button> }
            </mat-form-field>
          }
          @if (hasFilters) {
            <button mat-stroked-button type="button" class="filter-toggle" (click)="filtersOpen.update(toggle)" [attr.aria-expanded]="filtersOpen()" [attr.aria-controls]="filtersId"><mat-icon>filter_list</mat-icon>Filters</button>
          }
          <div class="table-filters" [class.open]="filtersOpen()" [id]="filtersId"><ng-content select="[filters]" /></div>
          @if (!loading() && total()) { <span class="result-count">{{ total().toLocaleString() }} records</span> }
        </div>
      }
      <div class="table-body">
        @if (loading() && rows().length) {
          <mat-progress-bar mode="indeterminate" class="!absolute left-0 right-0 top-0 z-10" />
        }
        @if (error()) {
          <app-error-state [message]="error()!" (retry)="retry.emit()" />
        } @else if (loading() && !rows().length) {
          <div class="loading-rows" aria-busy="true" aria-label="Loading">
            @for (i of skeletonRows; track i) {
              <app-skeleton height="1.75rem" />
            }
          </div>
        } @else if (!rows().length) {
          <app-empty-state [title]="emptyTitle()" [message]="emptyMessage()" [icon]="emptyIcon()" />
        } @else {
          <div class="table-scroll" tabindex="0" role="region" aria-label="Table, scroll horizontally to see all columns">
            <table mat-table [dataSource]="rows()" matSort (matSortChange)="onSort($event)" class="dense w-full">
              @for (col of columns(); track col.key) {
                <ng-container [matColumnDef]="col.key">
                  @if (col.sortable) {
                    <th mat-header-cell *matHeaderCellDef mat-sort-header [class.hide-mobile]="col.hideOnMobile"
                        [class.align-end]="col.align === 'end'">{{ col.label }}</th>
                  } @else {
                    <th mat-header-cell *matHeaderCellDef [class.hide-mobile]="col.hideOnMobile"
                        [class.align-end]="col.align === 'end'">{{ col.label }}</th>
                  }
                  <td mat-cell *matCellDef="let row" [class.hide-mobile]="col.hideOnMobile"
                      [class.align-end]="col.align === 'end'">
                    @if (templates().get(col.key); as tpl) {
                      <ng-container *ngTemplateOutlet="tpl; context: { $implicit: row }" />
                    } @else {
                      {{ display(col, row) }}
                    }
                  </td>
                </ng-container>
              }
              <tr mat-header-row *matHeaderRowDef="displayed()"></tr>
              <tr mat-row *matRowDef="let row; columns: displayed()" [class.row-clickable]="rowClickable()"
                  [attr.tabindex]="rowClickable() ? 0 : null"
                  (click)="rowClickable() && rowClick.emit(row)" (keydown.enter)="rowClickable() && rowClick.emit(row)" (keydown.space)="activateWithSpace($event, row)"></tr>
            </table>
          </div>
        }
      </div>
      @if (paginated() && total() > 0) {
        <mat-paginator [length]="total()" [pageIndex]="page() - 1" [pageSize]="pageSize()" [pageSizeOptions]="[10, 25, 50, 100]"
                       (page)="onPage($event)" aria-label="Select page" />
      }
    </div>
  `,
})
export class DataTableComponent<T> {
  readonly columns = input.required<Column<T>[]>();
  readonly rows = input<T[]>([]);
  readonly total = input(0);
  readonly page = input(1);
  readonly pageSize = input(25);
  readonly loading = input(false);
  readonly error = input<string | null>(null);
  readonly searchable = input(true);
  readonly paginated = input(true);
  readonly searchPlaceholder = input('Search');
  readonly emptyTitle = input('Nothing here yet');
  readonly emptyMessage = input<string | null>(null);
  readonly emptyIcon = input('inbox');
  readonly rowClickable = input(false);
  readonly hasFiltersInput = input(false, { alias: 'filters' });

  private static nextId = 0;
  readonly filtersId = `table-filters-${DataTableComponent.nextId++}`;
  readonly filtersOpen = signal(false);
  readonly searchValue = signal('');
  readonly toggle = (value: boolean) => !value;

  readonly pageChange = output<{ page: number; pageSize: number }>();
  readonly sortChange = output<string | null>();
  readonly search = output<string>();
  readonly retry = output<void>();
  readonly rowClick = output<T>();

  private readonly cellDefs = contentChildren(CellDefDirective);
  readonly templates = computed(() => new Map(this.cellDefs().map((d) => [d.name(), d.template])));
  readonly displayed = computed(() => this.columns().map((c) => c.key));
  readonly skeletonRows = [1, 2, 3, 4, 5, 6];
  private readonly search$ = new Subject<string>();

  get hasFilters(): boolean {
    return this.hasFiltersInput();
  }

  constructor() {
    this.search$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed(inject(DestroyRef)))
      .subscribe((q) => this.search.emit(q.trim()));
  }

  display(col: Column<T>, row: T): string | number {
    const value = col.cell ? col.cell(row) : (row as Record<string, unknown>)[col.key];
    return value === null || value === undefined || value === '' ? '—' : (value as string | number);
  }

  onSearch(value: string): void {
    this.searchValue.set(value);
    this.search$.next(value);
  }

  onSort(sort: Sort): void {
    this.sortChange.emit(sort.direction ? `${sort.direction === 'desc' ? '-' : ''}${sort.active}` : null);
  }

  activateWithSpace(event: Event, row: T): void {
    if (!this.rowClickable() || event.target !== event.currentTarget) return;
    event.preventDefault();
    this.rowClick.emit(row);
  }

  onPage(e: PageEvent): void {
    this.pageChange.emit({ page: e.pageIndex + 1, pageSize: e.pageSize });
  }
}

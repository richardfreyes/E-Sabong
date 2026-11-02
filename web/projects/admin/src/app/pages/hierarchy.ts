import { ChangeDetectionStrategy, Component, DestroyRef, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { RouterLink } from '@angular/router';
import { Subscription, forkJoin } from 'rxjs';

import { EmptyStateComponent, ErrorStateComponent, PageHeaderComponent, StatusChipComponent } from '@shared/components';
import type { HierarchyNode } from '@shared/models';
import { LabelPipe, PointsPipe } from '@shared/pipes';
import { AdminApi, BrandingService, toApiError, watchPoints } from '@shared/services';

interface TreeRow {
  node: HierarchyNode;
  depth: number;
  expanded: boolean;
  loading: boolean;
}

/** Lazily expanded account tree (master agent → agent → player). */
@Component({
  selector: 'app-admin-hierarchy',
  imports: [PageHeaderComponent, EmptyStateComponent, ErrorStateComponent, StatusChipComponent, MatButtonModule, MatIconModule, MatProgressBarModule,
            RouterLink, LabelPipe, PointsPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .tree-row { --tree-step: 1.5rem; padding-left: calc(0.5rem + var(--tree-depth) * var(--tree-step)); }
    .tree-row > button, .tree-spacer { flex: 0 0 44px; }
    .tree-identity { display: flex; flex-direction: column; gap: 2px; overflow-wrap: anywhere; }
    .tree-full-name { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
    .tree-balance { flex: none; width: 6rem; overflow-wrap: anywhere; }
    @media (max-width: 639px) { .tree-row { --tree-step: 0.75rem; } .tree-balance { width: 4rem; } }
  `,
  template: `
    <app-page-header title="Account hierarchy" subtitle="Expand a master agent or agent to see their downline." />
    <div class="rounded-2xl border border-outline-variant bg-surface-container-lowest">
      @if (loading()) { <mat-progress-bar mode="indeterminate" /> }
      @if (error()) {
        <app-error-state [message]="error()!" (retry)="loadRoot()" />
      } @else if (!loading() && !rows().length) {
        <app-empty-state icon="account_tree" title="No accounts in this view" message="Master agents, agents and players appear here once created." />
      } @else {
        <ul role="tree" aria-label="Account hierarchy" class="m-0 list-none p-2">
          @for (r of rows(); track r.node.id) {
            <li role="treeitem" [attr.aria-expanded]="r.node.child_count ? r.expanded : null" [attr.aria-level]="r.depth + 1"
                class="tree-row flex items-center gap-2 rounded-lg py-1 pr-2 hover:bg-surface-container" [style.--tree-depth]="r.depth">
              @if (r.node.child_count) {
                <button mat-icon-button type="button" (click)="toggle(r)" [attr.aria-label]="(r.expanded ? 'Collapse ' : 'Expand ') + r.node.username">
                  <mat-icon>{{ r.loading ? 'progress_activity' : r.expanded ? 'expand_more' : 'chevron_right' }}</mat-icon>
                </button>
              } @else {
                <span class="tree-spacer" aria-hidden="true"></span>
              }
              <a [routerLink]="['/users', r.node.id]" class="tree-identity min-w-0 flex-1 text-on-surface no-underline">
                <span class="font-medium">{{ r.node.username }}</span>
                <span class="tree-full-name text-sm text-on-surface-variant" [title]="r.node.full_name">{{ r.node.full_name }}</span>
              </a>
              <span class="hidden text-xs text-on-surface-variant sm:inline">{{ r.node.account_type | label }}</span>
              @if (r.node.child_count) { <span class="hidden text-xs text-on-surface-variant md:inline">{{ r.node.child_count }} below</span> }
              @if (branding.hasModule('points')) { <span class="tree-balance text-right text-sm tabular-nums">{{ r.node.balance | points }}</span> }
              @if (r.node.status !== 'active') { <app-status-chip [status]="r.node.status" /> }
            </li>
          }
        </ul>
      }
    </div>
  `,
})
export class HierarchyPage {
  private balanceRequest?: Subscription;
  private readonly destroy = inject(DestroyRef);
  readonly root = input<string | null>(null);
  readonly branding = inject(BrandingService);
  private readonly api = inject(AdminApi);
  readonly rows = signal<TreeRow[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  constructor() {
    watchPoints(() => this.refreshBalances());
    queueMicrotask(() => this.loadRoot());
  }

  private refreshBalances(): void {
    this.balanceRequest?.unsubscribe();
    const parents = [...new Set([this.root(), ...this.rows().filter(row => row.expanded).map(row => row.node.id)])];
    this.balanceRequest = forkJoin(parents.map(id => this.api.hierarchy(id))).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: groups => {
        const nodes = new Map(groups.flat().map(node => [node.id, node]));
        this.rows.update(rows => rows.map(row => nodes.has(row.node.id) ? { ...row, node: nodes.get(row.node.id)! } : row));
      },
      error: () => undefined,
    });
  }

  loadRoot(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.hierarchy(this.root()).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: (nodes) => {
        this.rows.set(nodes.map((node) => ({ node, depth: 0, expanded: false, loading: false })));
        this.loading.set(false);
      },
      error: (e) => (this.error.set(toApiError(e).message), this.loading.set(false)),
    });
  }

  toggle(row: TreeRow): void {
    const rows = this.rows();
    const index = rows.findIndex(r => r.node.id === row.node.id);
    if (index < 0 || rows[index].loading) return;
    if (row.expanded) {
      let end = index + 1;
      while (end < rows.length && rows[end].depth > row.depth) end++;
      this.rows.set([...rows.slice(0, index), { ...row, expanded: false }, ...rows.slice(end)]);
      return;
    }
    this.rows.set(rows.map((r) => (r === row ? { ...r, loading: true } : r)));
    this.api.hierarchy(row.node.id).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: (children) => {
        const current = this.rows();
        const i = current.findIndex((r) => r.node.id === row.node.id);
        // The parent may have been removed by an ancestor collapse while this read was pending.
        if (i < 0 || !current[i].loading) return;
        const kids = children.map((node) => ({ node, depth: row.depth + 1, expanded: false, loading: false }));
        this.rows.set([...current.slice(0, i), { ...current[i], expanded: true, loading: false }, ...kids, ...current.slice(i + 1)]);
      },
      error: () => this.rows.set(this.rows().map((r) => (r.node.id === row.node.id ? { ...r, loading: false } : r))),
    });
  }
}

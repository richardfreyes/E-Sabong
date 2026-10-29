import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatSelectModule } from '@angular/material/select';
import { MatFormFieldModule } from '@angular/material/form-field';
import { Router } from '@angular/router';

import { CellDefDirective, Column, DataTableComponent, PageHeaderComponent, StatusChipComponent } from '@shared/components';
import { HasPermissionDirective } from '@shared/directives';
import type { User, UserCreated } from '@shared/models';
import { LabelPipe, PointsPipe, RelativeTimePipe } from '@shared/pipes';
import { AdminApi, PagedList } from '@shared/services';
import { CreateUserDialog } from './user-form';

@Component({
  selector: 'app-admin-users',
  imports: [PageHeaderComponent, DataTableComponent, CellDefDirective, StatusChipComponent, MatButtonModule, MatIconModule, MatSelectModule,
            MatFormFieldModule, HasPermissionDirective, LabelPipe, PointsPipe, RelativeTimePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Accounts" subtitle="Administrators, operators, declarators, agents and players.">
      <button *appHasPermission="['users.manage', 'users.manage_downline']" mat-flat-button type="button" (click)="create()">
        <mat-icon>person_add</mat-icon>New account</button>
    </app-page-header>
    <app-data-table [columns]="columns" [rows]="list.rows()" [total]="list.total()" [page]="list.page()" [pageSize]="list.pageSize()"
                    [loading]="list.loading()" [error]="list.error()" [filters]="true" [rowClickable]="true" searchPlaceholder="Search username, name, email"
                    emptyTitle="No accounts match" emptyIcon="group" (search)="list.setSearch($event)" (sortChange)="list.setSort($event)"
                    (pageChange)="list.setPage($event)" (retry)="list.load()" (rowClick)="open($event)">
      <mat-form-field filters subscriptSizing="dynamic" class="dense w-44"><mat-label>Type</mat-label>
        <mat-select [value]="type()" (selectionChange)="setType($event.value)">
          <mat-option value="">All types</mat-option>
          @for (t of types; track t) { <mat-option [value]="t">{{ t | label }}</mat-option> }
        </mat-select></mat-form-field>
      <mat-form-field filters subscriptSizing="dynamic" class="dense w-40"><mat-label>Status</mat-label>
        <mat-select [value]="status()" (selectionChange)="setStatus($event.value)">
          <mat-option value="">Any status</mat-option><mat-option value="active">Active</mat-option><mat-option value="suspended">Suspended</mat-option>
        </mat-select></mat-form-field>
      <ng-template appCell="username" let-row>
        <div class="font-medium">{{ row.username }}</div><div class="text-xs text-on-surface-variant">{{ row.full_name }}</div>
      </ng-template>
      <ng-template appCell="account_type" let-row>{{ row.account_type | label }}</ng-template>
      <ng-template appCell="status" let-row><app-status-chip [status]="row.status" /></ng-template>
      <ng-template appCell="balance" let-row>{{ row.balance | points }}</ng-template>
      <ng-template appCell="last_login_at" let-row>{{ row.last_login_at | relativeTime }}</ng-template>
    </app-data-table>
  `,
})
export class UsersPage {
  private readonly api = inject(AdminApi);
  private readonly dialog = inject(MatDialog);
  private readonly router = inject(Router);
  readonly types = ['admin', 'operator', 'declarator', 'master_agent', 'agent', 'player'];
  readonly type = signal('');
  readonly status = signal('');
  readonly list = new PagedList<User>((q) => this.api.users(q));
  readonly columns: Column<User>[] = [
    { key: 'username', label: 'Account', sortable: true },
    { key: 'account_type', label: 'Type' },
    { key: 'parent_username', label: 'Parent', hideOnMobile: true },
    { key: 'status', label: 'Status' },
    { key: 'balance', label: 'Demo points', align: 'end', hideOnMobile: true },
    { key: 'last_login_at', label: 'Last sign-in', sortable: true, hideOnMobile: true },
  ];

  constructor() {
    this.list.load();
  }

  setType(v: string): void {
    this.type.set(v);
    this.list.setFilter('account_type', v || null);
  }

  setStatus(v: string): void {
    this.status.set(v);
    this.list.setFilter('status', v || null);
  }

  open(u: User): void {
    void this.router.navigate(['/users', u.id]);
  }

  create(): void {
    this.dialog.open(CreateUserDialog, { width: '640px', maxWidth: '95vw', disableClose: true })
      .afterClosed().subscribe((c?: UserCreated) => c && this.list.load());
  }
}

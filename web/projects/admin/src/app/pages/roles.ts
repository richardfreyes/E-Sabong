import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';

import { ConfirmService, ErrorStateComponent, FieldErrorComponent, LoadingComponent, PageHeaderComponent } from '@shared/components';
import type { Permission, Role } from '@shared/models';
import { LabelPipe } from '@shared/pipes';
import { AdminApi, ToastService, toApiError } from '@shared/services';

@Component({
  selector: 'app-admin-roles',
  imports: [PageHeaderComponent, LoadingComponent, ErrorStateComponent, FieldErrorComponent, ReactiveFormsModule, MatButtonModule, MatCheckboxModule,
            MatFormFieldModule, MatInputModule, MatIconModule, LabelPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Roles & permissions" subtitle="System roles are created with the workspace; add custom roles for finer control.">
      <button mat-flat-button type="button" (click)="startNew()"><mat-icon>add</mat-icon>New role</button>
    </app-page-header>
    @if (error()) {
      <app-error-state [message]="error()!" (retry)="load()" />
    } @else if (!roles().length) {
      <app-loading />
    } @else {
      <div class="grid gap-6 lg:grid-cols-[18rem_1fr]">
        <nav aria-label="Roles" class="flex flex-col gap-1">
          @for (r of roles(); track r.id) {
            <button type="button" (click)="select(r)" class="flex items-center justify-between rounded-xl border-0 px-3 py-2 text-left"
                    [class.bg-secondary-container]="selected()?.id === r.id" [class.bg-transparent]="selected()?.id !== r.id">
              <span><span class="block font-medium">{{ r.name | label }}</span>
                <span class="text-xs text-on-surface-variant">{{ r.is_system ? 'System' : 'Custom' }} · {{ r.user_count }} accounts</span></span>
              <mat-icon class="text-on-surface-variant" aria-hidden="true">chevron_right</mat-icon>
            </button>
          }
        </nav>
        <section class="rounded-2xl border border-outline-variant p-4">
          <form [formGroup]="form" (ngSubmit)="save()" novalidate>
            <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
              <mat-form-field><mat-label>Name</mat-label><input matInput formControlName="name" [readonly]="!!selected()" />
                <mat-hint>Lowercase letters, digits, - and _</mat-hint><mat-error><app-field-error [control]="form.controls.name" /></mat-error></mat-form-field>
              <mat-form-field><mat-label>Description</mat-label><input matInput formControlName="description" /></mat-form-field>
            </div>
            <fieldset class="m-0 mt-2 border-0 p-0" [disabled]="locked()">
              <legend class="mb-2 text-sm font-semibold">Permissions</legend>
              @if (locked()) { <p class="m-0 mb-2 text-sm text-on-surface-variant">The tenant administrator role always holds every permission.</p> }
              <div class="grid grid-cols-1 gap-1 md:grid-cols-2">
                @for (p of permissions(); track p.code) {
                  <mat-checkbox [checked]="granted().has(p.code)" (change)="togglePerm(p.code, $event.checked)" [disabled]="locked()">
                    <span class="font-mono text-xs">{{ p.code }}</span><span class="block text-xs text-on-surface-variant">{{ p.description }}</span>
                  </mat-checkbox>
                }
              </div>
            </fieldset>
            <div class="mt-4 flex flex-wrap gap-2">
              <button mat-flat-button type="submit" [disabled]="locked()">{{ selected() ? 'Save role' : 'Create role' }}</button>
              @if (selected() && !selected()!.is_system) {
                <button mat-button type="button" class="!text-error" (click)="remove(selected()!)">Delete role</button>
              }
            </div>
          </form>
        </section>
      </div>
    }
  `,
})
export class RolesPage {
  private readonly api = inject(AdminApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  readonly roles = signal<Role[]>([]);
  readonly permissions = signal<Permission[]>([]);
  readonly selected = signal<Role | null>(null);
  readonly granted = signal(new Set<string>());
  readonly error = signal<string | null>(null);
  readonly locked = computed(() => this.selected()?.name === 'tenant_admin');
  readonly form = inject(FormBuilder).nonNullable.group({
    name: ['', [Validators.required, Validators.minLength(2), Validators.pattern(/^[a-z0-9_-]+$/)]],
    description: [''],
  });

  constructor() {
    this.load();
    this.api.permissions().subscribe((p) => this.permissions.set(p));
  }

  load(keep?: string): void {
    this.api.roles().subscribe({
      next: (r) => {
        this.roles.set(r);
        const pick = r.find((x) => x.id === keep) ?? r[0];
        if (pick) this.select(pick);
      },
      error: (e) => this.error.set(toApiError(e).message),
    });
  }

  select(r: Role): void {
    this.selected.set(r);
    this.granted.set(new Set(r.permissions));
    this.form.reset({ name: r.name, description: r.description ?? '' });
  }

  startNew(): void {
    this.selected.set(null);
    this.granted.set(new Set());
    this.form.reset({ name: '', description: '' });
  }

  togglePerm(code: string, on: boolean): void {
    const next = new Set(this.granted());
    if (on) next.add(code);
    else next.delete(code);
    this.granted.set(next);
  }

  save(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const { name, description } = this.form.getRawValue();
    const permissions = [...this.granted()];
    const role = this.selected();
    const req = role
      ? this.api.updateRole(role.id, { description: description || null, permissions })
      : this.api.createRole({ name, description: description || null, permissions });
    req.subscribe({ next: (r) => (this.toast.success('Role saved'), this.load(r.id)), error: (e) => this.toast.error(e) });
  }

  async remove(r: Role): Promise<void> {
    if ((await this.confirm.ask({ title: `Delete ${r.name}`, message: 'Roles still assigned to accounts cannot be deleted.', confirmText: 'Delete', destructive: true })) === null) return;
    this.api.deleteRole(r.id).subscribe({ next: () => (this.toast.success('Role deleted'), this.load()), error: (e) => this.toast.error(e) });
  }
}

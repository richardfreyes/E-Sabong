import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, effect, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatMenuModule } from '@angular/material/menu';
import { Router, RouterLink } from '@angular/router';

import {
  ConfirmService,
  EmptyStateComponent,
  ErrorStateComponent,
  FieldErrorComponent,
  LoadingComponent,
  MatchCardComponent,
  PageHeaderComponent,
  StatusChipComponent,
  outcomeLabel,
} from '@shared/components';
import type { EventItem, Match, MatchOutcome, ResultHistoryEntry, TenantProfile } from '@shared/models';
import { AdminApi, AuthStore, RealtimeService, ToastService, toApiError } from '@shared/services';
import { DeclaratorAssignments } from '../declarator/assignments';
import { EventDialog, fromLocalInput, toLocalInput } from './events';

interface ResultDialogData {
  match: Match;
  correction: boolean;
}

@Component({
  selector: 'app-result-dialog',
  imports: [ReactiveFormsModule, MatDialogModule, MatButtonModule, MatButtonToggleModule, MatFormFieldModule, MatInputModule, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ data.correction ? 'Correct official result' : 'Declare official result' }}</h2>
    <mat-dialog-content>
      <p class="m-0 mb-3 text-sm text-on-surface-variant">Match #{{ data.match.match_number }}: <strong class="text-meron">{{ data.match.meron_name }}</strong>
        (Meron) vs <strong class="text-wala">{{ data.match.wala_name }}</strong> (Wala)</p>
      <form [formGroup]="form" id="result-form" (ngSubmit)="submit()" novalidate>
        <mat-button-toggle-group formControlName="outcome" class="mb-4 flex w-full flex-wrap" aria-label="Outcome">
          @for (o of outcomes; track o) { <mat-button-toggle [value]="o" class="flex-1">{{ label(o) }}</mat-button-toggle> }
        </mat-button-toggle-group>
        @if (data.correction) {
          <mat-form-field class="w-full"><mat-label>Reason for correction</mat-label><textarea matInput rows="2" formControlName="reason"></textarea>
            <mat-hint>Recorded in the result history and audit log</mat-hint><mat-error><app-field-error [control]="form.controls.reason" /></mat-error></mat-form-field>
        } @else {
          <mat-form-field class="w-full"><mat-label>Notes (optional)</mat-label><textarea matInput rows="2" formControlName="notes"></textarea></mat-form-field>
        }
        @if (error()) { <p class="m-0 text-sm text-error" role="alert">{{ error() }}</p> }
      </form>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close>Cancel</button>
      <button mat-flat-button type="submit" form="result-form" [disabled]="!form.controls.outcome.value">{{ data.correction ? 'Save correction' : 'Declare result' }}</button>
    </mat-dialog-actions>
  `,
})
export class ResultDialog {
  readonly data = inject<ResultDialogData>(MAT_DIALOG_DATA);
  private readonly api = inject(AdminApi);
  private readonly ref = inject(MatDialogRef<ResultDialog>);
  readonly outcomes: MatchOutcome[] = ['meron', 'wala', 'draw', 'no_contest'];
  readonly label = outcomeLabel;
  readonly error = signal<string | null>(null);
  readonly form = inject(FormBuilder).nonNullable.group({
    outcome: [this.data.match.result?.outcome ?? ('' as MatchOutcome | ''), Validators.required],
    notes: [''],
    reason: ['', this.data.correction ? [Validators.required, Validators.minLength(3)] : []],
  });

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    const outcome = v.outcome as MatchOutcome;
    const req = this.data.correction
      ? this.api.correctResult(this.data.match.id, outcome, v.reason)
      : this.api.declareResult(this.data.match.id, outcome, v.notes || null);
    req.subscribe({ next: (m) => this.ref.close(m), error: (e) => this.error.set(toApiError(e).message) });
  }
}

@Component({
  selector: 'app-history-dialog',
  imports: [MatDialogModule, MatButtonModule, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>Result history</h2>
    <mat-dialog-content>
      <ol class="m-0 flex list-none flex-col gap-3 p-0">
        @for (h of data; track h.id) {
          <li class="rounded-lg border border-outline-variant p-3 text-sm">
            <div class="font-medium">Revision {{ h.revision }}: {{ label(h.outcome) }}
              @if (h.previous_outcome) { <span class="text-on-surface-variant">(was {{ label(h.previous_outcome) }})</span> }</div>
            <div class="text-xs text-on-surface-variant">{{ h.created_at | date: 'medium' }} · {{ h.changed_by_username }}</div>
            @if (h.reason) { <div class="mt-1">{{ h.reason }}</div> }
          </li>
        }
      </ol>
    </mat-dialog-content>
    <mat-dialog-actions align="end"><button mat-button type="button" mat-dialog-close>Close</button></mat-dialog-actions>
  `,
})
export class HistoryDialog {
  readonly data = inject<ResultHistoryEntry[]>(MAT_DIALOG_DATA);
  readonly label = outcomeLabel;
}

@Component({
  selector: 'app-admin-event-detail',
  imports: [
    RouterLink, ReactiveFormsModule, DatePipe, MatButtonModule, MatIconModule, MatMenuModule, MatFormFieldModule, MatInputModule,
    DeclaratorAssignments, PageHeaderComponent, StatusChipComponent, LoadingComponent, ErrorStateComponent, EmptyStateComponent, MatchCardComponent, FieldErrorComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (error()) {
      <app-error-state [message]="error()!" (retry)="load()" />
    } @else if (!event()) {
      <app-loading />
    } @else {
      @let e = event()!;
      <app-page-header [title]="e.name" [subtitle]="(e.venue ? e.venue + ' · ' : '') + (e.starts_at | date: 'medium')">
        <a breadcrumb routerLink="/events" class="mb-1 inline-flex items-center gap-1 text-sm text-primary no-underline"><mat-icon class="!h-4 !w-4 !text-base">arrow_back</mat-icon>Events</a>
        <app-status-chip [status]="e.status" />
        @if (canManageEvent) {
          @switch (e.status) {
            @case ('draft') { <button mat-flat-button type="button" (click)="act('publish')"><mat-icon>publish</mat-icon>Publish</button> }
            @case ('scheduled') {
              <button mat-flat-button type="button" (click)="act('start')"><mat-icon>play_arrow</mat-icon>Go live</button>
              <button mat-stroked-button type="button" (click)="act('unpublish')">Unpublish</button>
            }
            @case ('live') { <button mat-flat-button type="button" (click)="act('complete')"><mat-icon>flag</mat-icon>Complete event</button> }
          }
          @if (e.status !== 'completed' && e.status !== 'cancelled') {
            <button mat-icon-button type="button" [matMenuTriggerFor]="menu" aria-label="More event actions"><mat-icon>more_vert</mat-icon></button>
            <mat-menu #menu="matMenu">
              <button mat-menu-item type="button" (click)="edit(e)"><mat-icon>edit</mat-icon>Edit details</button>
              <button mat-menu-item type="button" class="!text-error" (click)="cancelEvent()"><mat-icon>cancel</mat-icon>Cancel event</button>
              @if (e.status === 'draft') { <button mat-menu-item type="button" class="!text-error" (click)="deleteEvent()"><mat-icon>delete</mat-icon>Delete draft</button> }
            </mat-menu>
          }
        }
      </app-page-header>
      @if (e.description) { <p class="m-0 mb-6 max-w-3xl whitespace-pre-line text-sm text-on-surface-variant">{{ e.description }}</p> }

      @if (canManageEvent) { <app-declarator-assignments [eventId]="e.id" /> }

      @if (canManageMatches && e.status !== 'completed' && e.status !== 'cancelled') {
        <form [formGroup]="matchForm" (ngSubmit)="addMatch()" class="mb-6 grid grid-cols-1 gap-x-3 rounded-2xl border border-outline-variant p-4 sm:grid-cols-2 lg:grid-cols-5" novalidate>
          <h2 class="m-0 mb-2 text-base font-semibold sm:col-span-2 lg:col-span-5">Add match</h2>
          <mat-form-field><mat-label>Meron</mat-label><input matInput formControlName="meron_name" /><mat-error><app-field-error [control]="matchForm.controls.meron_name" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Meron details</mat-label><input matInput formControlName="meron_details" placeholder="Owner, weight…" /></mat-form-field>
          <mat-form-field><mat-label>Wala</mat-label><input matInput formControlName="wala_name" /><mat-error><app-field-error [control]="matchForm.controls.wala_name" /></mat-error></mat-form-field>
          <mat-form-field><mat-label>Wala details</mat-label><input matInput formControlName="wala_details" /></mat-form-field>
          <mat-form-field><mat-label>Scheduled</mat-label><input matInput type="datetime-local" formControlName="scheduled_at" /></mat-form-field>
          <div class="sm:col-span-2 lg:col-span-5"><button mat-flat-button type="submit"><mat-icon>add</mat-icon>Add match #{{ matches().length + 1 }}</button></div>
        </form>
      }

      @if (!matches().length) {
        <app-empty-state icon="sports_kabaddi" title="No matches yet" message="Add the fight card above. Matches can start once the event is live." />
      } @else {
        <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          @for (m of matches(); track m.id) {
            <app-match-card [match]="m">
              <div class="flex gap-1">
                @if (canManageEvent) { <app-declarator-assignments [eventId]="e.id" /> }

      @if (canManageMatches && m.status === 'scheduled' && e.status === 'live') {
                  <button mat-stroked-button type="button" (click)="startMatch(m)">Start</button>
                }
                @if (canDeclare && m.status === 'in_progress') {
                  <button mat-flat-button type="button" (click)="result(m, false)">Declare result</button>
                }
                <button mat-icon-button type="button" [matMenuTriggerFor]="mm" [attr.aria-label]="'Actions for match ' + m.match_number"><mat-icon>more_horiz</mat-icon></button>
                <mat-menu #mm="matMenu">
                  @if (m.result) {
                    <button mat-menu-item type="button" (click)="history(m)"><mat-icon>history</mat-icon>Result history</button>
                    @if (canCorrect) { <button mat-menu-item type="button" (click)="result(m, true)"><mat-icon>edit_note</mat-icon>Correct result</button> }
                  }
                  @if (canManageEvent) { <app-declarator-assignments [eventId]="e.id" /> }

      @if (canManageMatches && (m.status === 'scheduled' || m.status === 'in_progress')) {
                    <button mat-menu-item type="button" class="!text-error" (click)="cancelMatch(m)"><mat-icon>cancel</mat-icon>Cancel match</button>
                  }
                  @if (canManageEvent) { <app-declarator-assignments [eventId]="e.id" /> }

      @if (canManageMatches && (m.status === 'scheduled' || m.status === 'cancelled')) {
                    <button mat-menu-item type="button" class="!text-error" (click)="deleteMatch(m)"><mat-icon>delete</mat-icon>Delete</button>
                  }
                </mat-menu>
              </div>
            </app-match-card>
          }
        </div>
      }
    }
  `,
})
export class EventDetailPage {
  readonly id = input.required<string>();
  private readonly api = inject(AdminApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly dialog = inject(MatDialog);
  private readonly router = inject(Router);
  private readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);

  readonly event = signal<EventItem | null>(null);
  readonly matches = signal<Match[]>([]);
  readonly error = signal<string | null>(null);
  readonly canManageEvent = this.auth.hasPermission('events.manage');
  readonly canManageMatches = this.auth.hasPermission('matches.manage');
  readonly canDeclare = this.auth.hasPermission('results.declare');
  readonly canCorrect = this.auth.hasPermission('results.correct');
  readonly matchForm = inject(FormBuilder).nonNullable.group({
    meron_name: ['', [Validators.required, Validators.maxLength(120)]],
    meron_details: [''],
    wala_name: ['', [Validators.required, Validators.maxLength(120)]],
    wala_details: [''],
    scheduled_at: [''],
  });

  constructor() {
    effect(() => {
      this.id();
      this.load();
    });
    // Another operator's changes arrive over the WebSocket.
    inject(RealtimeService)
      .on<{ id: string; event_id?: string }>('match.updated', 'match.result_declared', 'match.result_corrected', 'event.updated')
      .pipe(takeUntilDestroyed(inject(DestroyRef)))
      .subscribe((d) => {
        if (d.id === this.id() || d.event_id === this.id()) this.load();
      });
  }

  load(): void {
    this.error.set(null);
    this.api.event(this.id()).subscribe({ next: (e) => this.event.set(e), error: (e) => this.error.set(toApiError(e).message) });
    this.api.matches(this.id()).subscribe({ next: (m) => this.matches.set(m), error: () => this.matches.set([]) });
  }

  act(action: 'publish' | 'unpublish' | 'start' | 'complete'): void {
    this.api.eventAction(this.id(), action).subscribe({ next: (e) => (this.event.set(e), this.toast.success(`Event ${e.status}`)), error: (e) => this.toast.error(e) });
  }

  edit(e: EventItem): void {
    this.dialog.open(EventDialog, { data: e, width: '600px', maxWidth: '95vw' }).afterClosed().subscribe((u?: EventItem) => u && this.event.set(u));
  }

  async cancelEvent(): Promise<void> {
    if ((await this.confirm.ask({ title: 'Cancel event', message: 'All scheduled and in-progress matches are cancelled too.', confirmText: 'Cancel event', destructive: true })) === null) return;
    this.api.eventAction(this.id(), 'cancel').subscribe({ next: () => this.load(), error: (e) => this.toast.error(e) });
  }

  async deleteEvent(): Promise<void> {
    if ((await this.confirm.ask({ title: 'Delete draft', message: 'The draft and its matches are removed.', confirmText: 'Delete', destructive: true })) === null) return;
    this.api.deleteEvent(this.id()).subscribe({ next: () => void this.router.navigate(['/events']), error: (e) => this.toast.error(e) });
  }

  addMatch(): void {
    if (this.matchForm.invalid) {
      this.matchForm.markAllAsTouched();
      return;
    }
    const v = this.matchForm.getRawValue();
    this.api.createMatch(this.id(), { meron_name: v.meron_name, meron_details: v.meron_details || null, wala_name: v.wala_name,
      wala_details: v.wala_details || null, scheduled_at: fromLocalInput(v.scheduled_at) }).subscribe({
      next: () => (this.matchForm.reset({ scheduled_at: toLocalInput(null) }), this.load()),
      error: (e) => this.toast.error(e),
    });
  }

  startMatch(m: Match): void {
    this.api.startMatch(m.id).subscribe({ next: () => this.load(), error: (e) => this.toast.error(e) });
  }

  result(m: Match, correction: boolean): void {
    this.dialog.open(ResultDialog, { data: { match: m, correction }, width: '520px', maxWidth: '95vw' }).afterClosed()
      .subscribe((u?: Match) => u && (this.toast.success(correction ? 'Result corrected' : 'Result declared'), this.load()));
  }

  history(m: Match): void {
    this.api.resultHistory(m.id).subscribe({ next: (h) => this.dialog.open(HistoryDialog, { data: h, width: '480px', maxWidth: '95vw' }), error: (e) => this.toast.error(e) });
  }

  async cancelMatch(m: Match): Promise<void> {
    const reason = await this.confirm.ask({ title: `Cancel match #${m.match_number}`, message: 'Cancelled matches keep their place on the card.', confirmText: 'Cancel match',
      destructive: true, inputLabel: 'Reason', inputRequired: true });
    if (reason === null) return;
    this.api.cancelMatch(m.id, reason).subscribe({ next: () => this.load(), error: (e) => this.toast.error(e) });
  }

  async deleteMatch(m: Match): Promise<void> {
    if ((await this.confirm.ask({ title: `Delete match #${m.match_number}`, message: 'The match is removed from the card.', confirmText: 'Delete', destructive: true })) === null) return;
    this.api.deleteMatch(m.id).subscribe({ next: () => this.load(), error: (e) => this.toast.error(e) });
  }
}

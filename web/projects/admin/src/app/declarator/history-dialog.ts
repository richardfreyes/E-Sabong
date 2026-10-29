import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { Subject, catchError, firstValueFrom, of, switchMap } from 'rxjs';
import { ConfirmService, LoadingComponent, StatusChipComponent } from '@shared/components';
import type { TenantProfile } from '@shared/models';
import { AuthStore, DECLARATION_LABELS, DeclaratorApi, DeclarationHistory, DeclarationOutcome, WORKFLOW_LABELS, RealtimeService, toApiError } from '@shared/services';

@Component({
  selector: 'app-declaration-history-dialog',
  imports: [DatePipe, FormsModule, MatDialogModule, MatButtonModule, MatFormFieldModule, MatInputModule, MatSelectModule,
    LoadingComponent, StatusChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .detail-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }
    .facts { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin:16px 0; }
    .facts dt { font-size:.7rem; color:var(--text-secondary); margin-bottom:4px; }
    .facts dd { margin:0; color:var(--text-primary); font-size:.85rem; }
    h3 { margin:24px 0 12px; font-size:.95rem; color:var(--text-primary); }
    ol { margin:0; padding:0; list-style:none; } li { border-left:2px solid var(--border); padding:0 0 18px 16px; font-size:.8rem; }
    li strong { color:var(--text-primary); } li p { margin:6px 0 0; white-space:pre-wrap; } li small { display:block; margin-top:5px; }
    .review-actions { display:flex; gap:8px; flex-wrap:wrap; margin:16px 0; }
    .amendment { display:grid; grid-template-columns:1fr 1fr; gap:12px; } .amendment button { grid-column:1/-1; justify-self:start; }
    .error { color:var(--error-text); } .proposal { padding:14px; background:var(--surface-elevated); border-radius:var(--radius-control); }
    @media(max-width:639px) { .amendment,.facts { grid-template-columns:1fr; } }
  `,
  template: `
    <h2 mat-dialog-title>{{ data() ? 'Fight #' + data()!.record.match.match_number : 'Result details' }}</h2>
    <mat-dialog-content [attr.aria-busy]="loading()">
      @if (error()) { <p class="error" role="alert">{{ error() }}</p><button mat-stroked-button type="button" (click)="load()">Reload result details</button> }
      @if (!data() && !error()) { <app-loading /> }
      @if (data(); as history) {
        @let record = history.record;
        <div class="detail-head"><strong>{{ record.event_name }}</strong><app-status-chip [status]="record.workflow_status" [text]="statuses[record.workflow_status]" /></div>
        <dl class="facts">
          <div><dt>Official result</dt><dd>{{ record.official_winner ? labels[record.official_winner] : 'Awaiting finalization' }}</dd></div>
          <div><dt>Fight status</dt><dd>{{ record.match.status.replaceAll('_',' ') }}</dd></div>
          <div><dt>Declared by</dt><dd>{{ record.declared_by ?? '—' }}<br />{{ record.declared_at | date:'medium' }}</dd></div>
          <div><dt>Approved by</dt><dd>{{ record.approved_by ?? 'Not recorded' }}<br />{{ record.finalized_at | date:'medium' }}</dd></div>
        </dl>
        @if (record.declaration; as proposal) {
          <div class="proposal"><strong>Proposed: {{ labels[proposal.outcome] }}</strong><p class="m-0 mt-1 text-sm">{{ statuses[proposal.status] }} · {{ proposal.base_revision ? 'Amendment to revision ' + proposal.base_revision : 'Initial declaration' }}</p>@if (proposal.reason) { <p class="m-0 mt-2 text-sm">{{ proposal.reason }}</p> }</div>
          @if (auth.hasPermission('results.approve') && active() && proposal.declared_by_id !== auth.user()?.id) {
            <div class="review-actions"><button mat-flat-button [disabled]="busy()" [attr.aria-busy]="busy()" (click)="review('approve')">Confirm official result</button><button mat-stroked-button [disabled]="busy()" (click)="review('review')">Under review</button><button mat-button [disabled]="busy()" (click)="review('return')">Return declaration</button></div>
          }
        }
        @if (canAmend()) {
          <h3>Request an amendment</h3><p class="text-sm">The original result stays official until an authorized reviewer approves the amendment.</p>
          <div class="amendment"><mat-form-field><mat-label>Proposed result</mat-label><mat-select [(ngModel)]="amendOutcome">@for (outcome of amendOutcomes; track outcome) { <mat-option [value]="outcome">{{ labels[outcome] }}</mat-option> }</mat-select></mat-form-field><mat-form-field><mat-label>Reason</mat-label><textarea matInput [(ngModel)]="amendReason" maxlength="500" rows="2"></textarea></mat-form-field><button mat-stroked-button [disabled]="busy() || amendReason.trim().length < 3 || !amendOutcome" (click)="amend()">Submit amendment</button></div>
        }
        <h3>Declaration audit trail</h3>
        <ol aria-label="Declaration audit trail">@for (entry of history.audit; track entry.id) { <li><strong>{{ entry.actor }} · {{ entry.action }} · {{ auditOutcome(entry.declaration_id) }}</strong><small>{{ entry.created_at | date:'medium' }} · {{ entry.to_status.replaceAll('_',' ') }}</small>@if (entry.reason) { <p>{{ entry.reason }}</p> }</li> } @empty { <li>No declaration workflow entries for this fight.</li> }</ol>
        <h3>Official revisions</h3><ol aria-label="Official revisions">@for (revision of history.revisions; track revision.id) { <li><strong>Revision {{ revision.revision }} · {{ labels[revision.outcome] }}</strong><small>{{ revision.created_at | date:'medium' }} · {{ revision.changed_by_username ?? 'Unavailable account' }}</small>@if (revision.reason) { <p>{{ revision.reason }}</p> }</li> } @empty { <li>No official result revision recorded.</li> }</ol>
      } @else if (error()) { <button mat-stroked-button (click)="load()">Try again</button> }
    </mat-dialog-content>
    <mat-dialog-actions align="end"><button mat-button mat-dialog-close>Close</button></mat-dialog-actions>
  `,
})
export class DeclarationHistoryDialog {
  private readonly input = inject<{matchId: string}>(MAT_DIALOG_DATA);
  private readonly api = inject(DeclaratorApi);
  readonly auth = inject<AuthStore<TenantProfile>>(AuthStore);
  private readonly confirm = inject(ConfirmService);
  private readonly ref = inject(MatDialogRef<DeclarationHistoryDialog>);
  readonly data = signal<DeclarationHistory | null>(null);
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);
  readonly loading = signal(true);
  readonly labels = DECLARATION_LABELS;
  readonly statuses = WORKFLOW_LABELS;
  readonly amendOutcomes: DeclarationOutcome[] = ['meron','wala','draw','no_contest'];
  amendOutcome: DeclarationOutcome | '' = '';
  amendReason = '';
  private readonly requestKeys = new Map<string,string>();
  private readonly refresh$ = new Subject<void>();
  constructor() {
    this.refresh$.pipe(switchMap(() => this.api.history(this.input.matchId).pipe(catchError(e => {
      this.error.set(toApiError(e).message); return of(null);
    }))),takeUntilDestroyed()).subscribe(data => { if(data) this.data.set(data); this.loading.set(false); });
    inject(RealtimeService).on<{id?:string; match_id?:string}>('connected','declaration.updated','match.result_declared','match.result_corrected','declaration.assignment_changed').pipe(takeUntilDestroyed()).subscribe(message => {
      if(!message.id || message.id === this.input.matchId || message.match_id === this.input.matchId) this.load();
    });
    this.load();
  }
  load(): void { this.error.set(null); this.loading.set(true); this.refresh$.next(); }
  auditOutcome(id: string): string {
    const declaration = this.data()?.declarations.find(item => item.id === id);
    return declaration ? this.labels[declaration.outcome] : 'Outcome unavailable';
  }
  active(): boolean { return !this.loading() && !this.error() && ['pending_approval','under_review'].includes(this.data()?.record.declaration?.status ?? ''); }
  canAmend(): boolean { return !this.loading() && !this.error() && this.auth.user()?.account_type === 'declarator' && this.auth.hasPermission('results.submit') && !this.active() && this.data()?.record.match.status === 'completed'; }
  async review(action: 'approve'|'review'|'return'): Promise<void> {
    const d = this.data()?.record.declaration;
    if (!d || !this.active() || this.busy()) return;
    this.busy.set(true); this.error.set(null);
    try {
      const value = await this.confirm.ask({title: action === 'approve' ? 'Finalize official result' : action === 'review' ? 'Mark under review' : 'Return declaration',
        message: `${this.labels[d.outcome]} · Fight #${this.data()!.record.match.match_number} · ${this.data()!.record.event_name}. ${action === 'approve' ? 'Publish this as the official result?' : 'The current official result will stay unchanged.'}`,
        confirmText: action === 'approve' ? 'Confirm Finalization' : 'Confirm',
        ...(action !== 'approve' ? {inputLabel:'Reason',inputRequired:true,inputMinLength:3} : {}),
      });
      if (value === null) return;
      await firstValueFrom(action === 'approve' ? this.api.approve(d.id) : action === 'review' ? this.api.review(d.id,value) : this.api.returnDeclaration(d.id,value));
      this.ref.close(true);
    } catch(e) { this.error.set(toApiError(e).message); } finally { this.busy.set(false); }
  }
  async amend(): Promise<void> {
    if (!this.canAmend() || this.busy() || !this.amendOutcome || this.amendReason.trim().length < 3) return;
    const record = this.data()!.record, outcome = this.amendOutcome, reason = this.amendReason.trim();
    this.busy.set(true); this.error.set(null);
    try {
      const yes = await this.confirm.ask({title:'Confirm amendment request',message:`Propose ${this.labels[outcome]} for Fight #${record.match.match_number} at ${record.event_name}? Reason: ${reason}`,confirmText:'Confirm Declaration'});
      if (yes === null) return;
      const key = `${outcome}:${reason}`, request = this.requestKeys.get(key) ?? crypto.randomUUID();
      this.requestKeys.set(key, request);
      await firstValueFrom(this.api.submit(record.match.id,{request_id:request,outcome,base_revision:record.match.result?.revision ?? 0,reason}));
      this.ref.close(true);
    } catch(e) { this.error.set(toApiError(e).message); } finally { this.busy.set(false); }
  }
}

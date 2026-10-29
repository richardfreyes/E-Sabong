import { ChangeDetectionStrategy, Component, inject, input, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { forkJoin, firstValueFrom } from 'rxjs';
import { DeclaratorApi, EventAssignment, toApiError } from '@shared/services';

@Component({
  selector: 'app-declarator-assignments',
  imports: [MatButtonModule, MatFormFieldModule, MatSelectModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `details {margin-bottom:24px;} summary {font-size:.85rem; cursor:pointer;} .body {display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:16px;} ul {padding:0;list-style:none;width:100%;margin:0;} li {display:flex;justify-content:space-between;align-items:center;font-size:.8rem;border-top:1px solid var(--border);padding-top:8px;} .error {color:var(--error-text);font-size:.8rem;} mat-form-field {min-width:200px;flex:1;} .hint {font-size:.75rem;color:var(--text-secondary);}`,
  template: `
    <details class="arena-panel" (toggle)="opened($event)"><summary>Declarator assignments</summary>
      <p class="hint">Assign Declarator accounts to monitor this event and submit results for independent approval.</p>
      @if (error()) { <p class="error" role="alert">{{ error() }}</p><button mat-button (click)="load()">Retry</button> }
      <div class="body"><mat-form-field subscriptSizing="dynamic"><mat-label>Declarator</mat-label><mat-select [value]="selected()" (selectionChange)="selected.set($event.value)">@for (user of users();track user.id) { <mat-option [value]="user.id">{{ user.username }}</mat-option> }</mat-select></mat-form-field><button mat-stroked-button [disabled]="!selected() || busy()" [attr.aria-busy]="busy()" (click)="change(selected(),false)">Assign</button>
      <ul>@for (assignment of assignments();track assignment.user_id) { <li><span>{{ assignment.username }}</span><button mat-button [disabled]="busy()" (click)="change(assignment.user_id,true)">Remove assignment</button></li> } @empty { <li>No Declarators assigned.</li> }</ul></div>
    </details>
  `,
})
export class DeclaratorAssignments {
  readonly eventId = input.required<string>();
  private readonly api = inject(DeclaratorApi);
  readonly assignments = signal<EventAssignment[]>([]);
  readonly users = signal<{id:string;username:string}[]>([]);
  readonly selected = signal('');
  readonly busy = signal(false);
  readonly error = signal<string|null>(null);
  opened(event: Event): void { if ((event.target as HTMLDetailsElement).open) this.load(); }
  load(): void {
    this.error.set(null);
    forkJoin({assignments:this.api.assignments(this.eventId()),users:this.api.eligibleUsers()}).subscribe({next: d => { this.assignments.set(d.assignments);this.users.set(d.users); },error:e=>this.error.set(toApiError(e).message)});
  }
  async change(user:string, remove:boolean): Promise<void> {
    if(this.busy()) return; this.busy.set(true);this.error.set(null);
    try { await firstValueFrom(remove ? this.api.unassign(this.eventId(),user) : this.api.assign(this.eventId(),user));this.load(); }
    catch(e) {this.error.set(toApiError(e).message);} finally {this.busy.set(false);}
  }
}

import { Component, inject, signal, ChangeDetectionStrategy } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { AgentUsersApi, toApiError } from '@shared/services';
import type { UserCreated } from '@shared/models';
import { SecretRevealComponent } from '@shared/components';

@Component({
  selector:'app-create-downline-dialog',
  imports:[ReactiveFormsModule,MatDialogModule,MatFormFieldModule,MatInputModule,MatButtonModule,SecretRevealComponent],
  changeDetection:ChangeDetectionStrategy.OnPush,
  template:`
    <h2 mat-dialog-title>{{ created() ? 'Account created' : 'Create ' + label }}</h2>
    <mat-dialog-content>
      @if (created(); as result) {
        <p>{{ result.user.username }} was created directly under your account.</p>
        @if (result.temporary_password) { <app-secret-reveal label="Temporary password — share securely" [value]="result.temporary_password" /> }
      } @else {
        <form id="downline-create" [formGroup]="form" (ngSubmit)="save()" class="flex flex-col gap-3 pt-2">
          <mat-form-field><mat-label>Username</mat-label><input matInput formControlName="username" autocomplete="off" /><mat-error>3–64 letters, digits, dots, underscores or hyphens</mat-error></mat-form-field>
          <mat-form-field><mat-label>Full name</mat-label><input matInput formControlName="full_name" /><mat-error>Full name is required</mat-error></mat-form-field>
          <p>A temporary password will be generated. The account must change it at first sign-in.</p>
          @if (error()) { <p role="alert">{{ error() }}</p> }
        </form>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      @if (created()) { <button mat-flat-button [mat-dialog-close]="true">Done</button> }
      @else {
        <button mat-button mat-dialog-close [disabled]="busy()">Cancel</button>
        <button mat-flat-button form="downline-create" type="submit" [disabled]="busy() || form.invalid">Create account</button>
      }
    </mat-dialog-actions>
  `,
})
export class CreateDownlineDialog {
  readonly type = inject<'player'|'sub_agent'>(MAT_DIALOG_DATA);
  readonly label = this.type === 'sub_agent' ? 'Sub-Agent' : 'Player';
  private readonly api = inject(AgentUsersApi);
  readonly busy=signal(false);readonly error=signal<string|null>(null);readonly created=signal<UserCreated|null>(null);
  readonly form=inject(FormBuilder).nonNullable.group({
    username:['',[Validators.required,Validators.minLength(3),Validators.maxLength(64),Validators.pattern(/^[A-Za-z0-9_.-]+$/)]],
    full_name:['',[Validators.required,Validators.maxLength(200)]],
  });
  save():void {
    if(this.form.invalid || this.busy()) return;
    this.busy.set(true);this.error.set(null);
    this.api.create({...this.form.getRawValue(),account_type:this.type}).subscribe({
      next:result=>{this.created.set(result);this.busy.set(false);},
      error:error=>{this.error.set(toApiError(error).message);this.busy.set(false);},
    });
  }
}

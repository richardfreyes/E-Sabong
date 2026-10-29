import { ChangeDetectionStrategy, Component } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { RouterLink } from '@angular/router';

import { EmptyStateComponent } from '@shared/components';

@Component({
  selector: 'app-forbidden',
  imports: [EmptyStateComponent, MatButtonModule, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-empty-state icon="lock" title="You don't have access to this page" message="Ask a tenant administrator to grant the permission you need.">
      <a mat-stroked-button routerLink="/profile">Go to your profile</a>
    </app-empty-state>
  `,
})
export class ForbiddenPage {}

import { ChangeDetectionStrategy, Component } from '@angular/core';

import { TenantProfilePage } from '@shared/components';

@Component({
  selector: 'app-admin-profile',
  imports: [TenantProfilePage],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<app-tenant-profile />',
})
export class ProfilePage {}

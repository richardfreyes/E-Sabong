import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { of } from 'rxjs';

import { ConfirmService } from '@shared/components';
import { AdminApi, ToastService } from '@shared/services';

import { LoaderDashboard } from './dashboard';

describe('LoaderDashboard', () => {
  it('shows the allocation and the accounts a loader may fund', () => {
    const api = {
      loaderAllocation: () => of({ balance: '80.00', username: 'loader1' }),
      loaderAccounts: () => of({
        items: [{
          id: 'p1', username: 'ply1', full_name: 'Player', account_type: 'player', status: 'active',
          parent_id: 'm1', parent_username: 'boss', balance: '10.00',
        }],
        total: 1, page: 1, page_size: 100,
      }),
      loaderHistory: () => of({ items: [], total: 0, page: 1, page_size: 50 }),
    };
    TestBed.configureTestingModule({
      providers: [
        provideNoopAnimations(),
        { provide: AdminApi, useValue: api },
        { provide: ToastService, useValue: { success: () => undefined, error: () => undefined } },
        { provide: ConfirmService, useValue: { ask: async () => '' } },
      ],
    });
    const fixture = TestBed.createComponent(LoaderDashboard);
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent!;
    expect(text).toContain('Available allocation');
    expect(text).toContain('80.00');
    expect(text).toContain('ply1');
    expect(text).toContain('Add balance');
    expect(text).toContain('Balance history');
  });
});

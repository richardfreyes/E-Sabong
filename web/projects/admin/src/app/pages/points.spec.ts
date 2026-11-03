import { TestBed } from '@angular/core/testing';
import { MatDialog } from '@angular/material/dialog';
import { EMPTY, of, throwError } from 'rxjs';
import { AdminApi, RealtimeService } from '@shared/services';
import { PointsPage } from './points';

async function setup() {
  const api = {
    pointsSummary: vi.fn(() => of({ own_balance: 600, circulating: 850, completed_transactions: 6 })),
    pointAccounts: vi.fn(() => of({ items: [{ user_id: 'p1', username: 'archived-player', balance: 200 }], total: 1, page: 1, page_size: 25 })),
    transactions: vi.fn(() => of({ items: [], total: 0, page: 1, page_size: 25 })),
    users: vi.fn(() => of({ items: [], total: 0, page: 1, page_size: 20 })),
  };
  TestBed.configureTestingModule({ providers: [
    { provide: AdminApi, useValue: api }, { provide: RealtimeService, useValue: { on: () => EMPTY } }, { provide: MatDialog, useValue: { open: vi.fn() } },
  ] });
  const fixture = TestBed.createComponent(PointsPage);
  fixture.detectChanges();
  await fixture.whenStable();
  return { fixture, page: fixture.componentInstance, api };
}

describe('Legacy points archive', () => {
  it('preserves actual historical balances with no mutation controls', async () => {
    const { fixture } = await setup();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Legacy points archive');
    expect(el.textContent).toContain('600');
    expect(el.textContent).toContain('850');
    expect(el.textContent).toContain('archived-player');
    expect(el.textContent).toContain('200');
    expect(el.querySelector('form')).toBeNull();
    expect(el.textContent).not.toMatch(/Transfer or allocate|Create Points|Transfer Points|Refund transfer/);
  });

  it('shows a retryable server error without inventing balances', async () => {
    const { fixture, page, api } = await setup();
    page.summary.set(null);
    api.pointsSummary.mockReturnValueOnce(throwError(() => new Error('Archive unavailable')));
    page.loadSummary();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role="alert"]')).not.toBeNull();
    expect(page.summary()).toBeNull();
    page.loadSummary();
    expect(page.summary()?.own_balance).toBe(600);
    expect(page.summaryError()).toBeNull();
  });
});

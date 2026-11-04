import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { AdminApi, BrandingService, ToastService } from '@shared/services';
import { ReportsPage } from './reports';

describe('ReportsPage refresh ordering', () => {
  it('cancels the previous report and keeps columns paired with their loaded data', () => {
    const activity = new Subject<any>();
    const points = new Subject<any>();
    TestBed.configureTestingModule({ providers: [
      { provide: AdminApi, useValue: { activityReport: () => activity, pointsReport: () => points } },
      { provide: BrandingService, useValue: { config: signal(null) } },
      { provide: ToastService, useValue: {} },
    ] });
    const page = TestBed.runInInjectionContext(() => new ReportsPage());
    const initial = { rows: [{ date: '2026-10-30', logins: 12 }], totals: {}, timezone: 'UTC', scope: 'all' };
    activity.next(initial);
    page.setKind('points');
    expect(activity.observed).toBe(false);
    expect(page.report()).toBe(initial);
    expect(page.columns()[0].key).toBe('date');
    activity.next({ ...initial, rows: [] });
    expect(page.report()).toBe(initial);
    points.next({ ...initial, rows: [{ username: 'player1', balance: 123 }] });
    expect(page.columns()[0].key).toBe('username');
    expect(page.reportKind()).toBe('points');
    expect(page.loading()).toBe(false);
    TestBed.resetTestingModule();
    expect(points.observed).toBe(false);
  });
});

import { TestBed } from '@angular/core/testing';
import { Subject, filter, map, of } from 'rxjs';
import { BrandingService, PortalApi, RealtimeService } from '@shared/services';
import { PlayerSession } from './player-session';

describe('PlayerSession points synchronization', () => {
  afterEach(() => { TestBed.resetTestingModule(); vi.useRealTimers(); });

  it('reads authoritative balances after updates and reconnects without applying pushed numbers', () => {
    vi.useFakeTimers();
    const messages = new Subject<{ type: string; data: unknown }>();
    const pending = new Subject<{ balance: number }>();
    const api = {
      points: vi.fn().mockReturnValueOnce(of({ balance: 25 })).mockReturnValueOnce(pending).mockReturnValueOnce(of({ balance: 75 })),
      notifications: () => of({ items: [], total: 0 }),
    };
    TestBed.configureTestingModule({ providers: [
      { provide: PortalApi, useValue: api },
      { provide: BrandingService, useValue: { hasModule: () => true } },
      { provide: RealtimeService, useValue: { on: (...types: string[]) => messages.pipe(filter(m => types.includes(m.type)), map(m => m.data)) } },
    ] });
    const session = TestBed.inject(PlayerSession);
    session.load();
    expect(session.balance()).toBe(25);
    messages.next({ type: 'points.received', data: { balance: 99999 } });
    expect(session.balance()).toBe(25);
    messages.next({ type: 'points.updated', data: {} });
    vi.advanceTimersByTime(200);
    expect(session.balance()).toBe(25);
    pending.next({ balance: 50 });
    expect(session.balance()).toBe(50);
    messages.next({ type: 'connected', data: {} });
    vi.advanceTimersByTime(200);
    expect(session.balance()).toBe(75);
  });
});

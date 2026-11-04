import { TestBed } from '@angular/core/testing';
import { Subject, filter, map, of } from 'rxjs';
import { PortalApi, RealtimeService } from '@shared/services';
import { PlayerSession } from './player-session';

describe('PlayerSession', () => {
  afterEach(() => TestBed.resetTestingModule());

  function setup() {
    const messages = new Subject<{ type: string; data: unknown }>();
    const api = { notifications: vi.fn(() => of({ items: [], total: 3 })), points: vi.fn() };
    TestBed.configureTestingModule({ providers: [
      { provide: PortalApi, useValue: api },
      { provide: RealtimeService, useValue: { on: (...types: string[]) => messages.pipe(filter(m => types.includes(m.type)), map(m => m.data)) } },
    ] });
    return { session: TestBed.inject(PlayerSession), messages, api };
  }

  it('counts notifications and never reads or holds a legacy points balance', () => {
    const { session, messages, api } = setup();
    session.load();
    expect(session.unread()).toBe(3);
    expect('balance' in session).toBe(false);
    messages.next({ type: 'points.updated', data: {} });
    messages.next({ type: 'points.received', data: { balance: 99999 } });
    expect(api.points).not.toHaveBeenCalled();
    messages.next({ type: 'notification.created', data: { id: 'n1', type: 'announcement', title: 'Hello', body: '', data: null } });
    expect(session.unread()).toBe(4);
    expect(session.recent()[0].title).toBe('Hello');
  });
});

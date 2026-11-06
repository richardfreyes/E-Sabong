import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { PortalApi, RealtimeService, ToastService } from '@shared/services';
import { PlayWalletStore } from '../core/play-betting';
import { CashoutRequestPage } from './cashout';

describe('Cashout request', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('sends the amount the player entered', () => {
    const created = {
      id: 'r1', player_id: 'p', player_username: 'player1', upline_id: 'a', upline_username: 'agent1',
      kind: 'cashout' as const, amount: '15.00', status: 'pending' as const, created_at: '2026-11-06T05:00:00Z',
      decided_at: null, replayed: false,
    };
    const rows: typeof created[] = [];
    const requestInbox = vi.fn(() => { rows.push(created); return of(created); });
    TestBed.configureTestingModule({
      providers: [
        { provide: PortalApi, useValue: { inbox: vi.fn(() => of(rows)), requestInbox } },
        { provide: PlayWalletStore, useValue: { balance: signal('100.00'), load: vi.fn() } },
        { provide: RealtimeService, useValue: { on: () => of() } },
        { provide: ToastService, useValue: { success: vi.fn(), error: vi.fn(), info: vi.fn() } },
      ],
    });
    const fixture = TestBed.createComponent(CashoutRequestPage);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const input = el.querySelector('[data-field="cashout-amount"]') as HTMLInputElement;
    input.value = '15';
    input.dispatchEvent(new Event('input'));
    (el.querySelector('[data-action="request-cashout"]') as HTMLButtonElement).click();
    expect(requestInbox).toHaveBeenCalledWith({ amount: '15.00', idempotency_key: expect.stringMatching(/^inbox-/) });
    fixture.detectChanges();
    expect(el.querySelector('[data-field="open-request"]')?.textContent).toContain('agent1');
    expect(el.querySelector('[data-field="open-request"]')?.textContent).toContain('15.00');
  });
});
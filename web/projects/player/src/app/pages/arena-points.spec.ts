import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';
import { PortalApi, RealtimeService } from '@shared/services';
import { PlayWalletStore } from '../core/play-betting';
import { ArenaPointsPage } from './arena-points';

async function setup() {
  const balance = signal<string | null>('1234.56');
  const messages = new Subject<unknown>();
  const wallet = { balance, load: vi.fn() };
  const api = { arenaPointsHistory: vi.fn(() => of({ items: [{ id: 'entry', kind: 'stake', delta: '-25.00', balance_after: '1234.56', created_at: '2026-11-02T01:00:00Z' }], total: 1, page: 1, page_size: 25 })) };
  TestBed.configureTestingModule({ providers: [
    { provide: PortalApi, useValue: api }, { provide: PlayWalletStore, useValue: wallet },
    { provide: RealtimeService, useValue: { on: () => messages } },
  ] });
  const fixture = TestBed.createComponent(ArenaPointsPage);
  fixture.detectChanges();
  await fixture.whenStable();
  return { fixture, wallet, api, messages };
}

describe('Balance statement', () => {
  it('uses the shared gameplay balance and actual ledger amounts', async () => {
    const { fixture, wallet } = await setup();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-field="balance"]')?.textContent).toBe('1,234.56');
    expect(el.textContent).toContain('−25.00');
    expect(el.textContent).toContain('Balance 1,234.56');
    expect(el.textContent).not.toMatch(/\b(?:cr|pts|credits|PHP)\b|₱/i);
    wallet.balance.set(null);
    fixture.detectChanges();
    expect(el.querySelector('[data-field="balance"]')?.textContent).toBe('—');
  });

  it('refreshes entries on balance changes and reconnects without issuing writes', async () => {
    const { fixture, wallet, api, messages } = await setup();
    const before = api.arenaPointsHistory.mock.calls.length;
    wallet.balance.set('1000.00');
    fixture.detectChanges();
    expect(api.arenaPointsHistory.mock.calls.length).toBe(before + 1);
    messages.next({});
    expect(api.arenaPointsHistory.mock.calls.length).toBe(before + 2);
    expect(wallet.load).toHaveBeenCalledTimes(1);
  });
});

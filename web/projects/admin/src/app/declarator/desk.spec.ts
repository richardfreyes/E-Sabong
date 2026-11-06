import { NO_ERRORS_SCHEMA, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { TestBed } from '@angular/core/testing';
import { MatDialog } from '@angular/material/dialog';
import { of, throwError } from 'rxjs';
import { ConfirmService } from '@shared/components';
import { ArenaPointsPipe } from '@shared/pipes';
import { ApiError, BettingControl, GhostAuto, OnlinePlayers } from '@shared/services';
import { DeclaratorDashboard } from './dashboard';
import { HistoryBeadsComponent, MIN_BEAD_COLUMNS } from './history-beads';
import { DeclaratorStore } from './store';

const open: BettingControl = { match_id: 'f7', match_number: 7, match_status: 'in_progress', status: 'open', book_state: 'open',
  can_open: false, can_close: true, blocked_reason: null, next_match_number: null };
const running: GhostAuto = { active: true, id: 'g1', match_id: 'f7', match_number: 7, meron_amount: '3628.00', wala_amount: '3888.00',
  interval_seconds: 5, runs: 3, added_meron: '10884.00', added_wala: '11664.00', started_by: 'judge' };

function desk(answer: string | null = '') {
  const confirm = { ask: vi.fn(() => Promise.resolve(answer)) };
  TestBed.configureTestingModule({ providers: [{ provide: ConfirmService, useValue: confirm }, { provide: MatDialog, useValue: { open: vi.fn() } }] });
  const ghost = signal<GhostAuto | null>(null);
  const monitor = { event: { id: 'ev', name: 'Main', status: 'live' },
    matches: [{ id: 'f6', match_number: 6, status: 'completed', meron_name: 'Old', wala_name: 'Fight', result: { outcome: 'meron', revision: 1 } },
      { id: 'f7', match_number: 7, status: 'in_progress', meron_name: 'Delta Alpha', wala_name: 'Silver Stallion' }] };
  const store = {
    submitter: () => true, approver: () => false, auth: { user: () => ({ username: 'judge' }) }, realtime: { state: () => 'open', disconnect: vi.fn(), connect: vi.fn() },
    liveEnabled: () => true, eventId: () => 'ev', events: () => [monitor.event], monitor: () => monitor,
    current: () => ({ match: monitor.matches[1] }), currentBook: () => null, betting: () => open, settlements: () => [], loading: () => false,
    error: () => null, approvalNotice: () => '', canSubmit: () => false, denied: () => false, empty: () => false, submitHint: () => null,
    onlinePlayers: () => 2, ghost, onlineList: signal<OnlinePlayers | null>({ players: 2, fight_number: 7, items: [
      { id: 'u1', username: 'ana', full_name: 'Ana Cruz', balance: '850.00', meron_stake: '150.00', wala_stake: '0.00', draw_stake: '0.00', last_seen_at: '' },
      { id: 'u2', username: 'ben', full_name: 'ben', balance: '1000.00', meron_stake: '0.00', wala_stake: '0.00', draw_stake: '0.00', last_seen_at: '' },
    ] }),
    records: { rows: () => [], loading: () => false, error: () => null, total: () => 0, page: () => 1, pageSize: () => 25, filter: () => null },
    api: {
      jumpToFight: vi.fn(() => of({ id: 'f196', match_number: 196 })),
      startGhostAuto: vi.fn(() => of(running)), stopGhostAuto: vi.fn(() => of({ ...running, active: false, stop_reason: 'Stopped by judge' })),
      endEvent: vi.fn(() => of({})), refreshAll: vi.fn(() => of({ message: 'ok' })),
    },
    refresh: vi.fn(), reconcile: vi.fn(),
  };
  TestBed.overrideComponent(DeclaratorDashboard, { set: { imports: [DatePipe, ArenaPointsPipe], schemas: [NO_ERRORS_SCHEMA], providers: [{ provide: DeclaratorStore, useValue: store }] } });
  const fixture = TestBed.createComponent(DeclaratorDashboard); fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  const q = <T extends HTMLElement>(id: string) => el.querySelector(`[data-testid="${id}"]`) as T;
  const type = (id: string, value: string) => { const input = q<HTMLInputElement>(id); input.value = value; input.dispatchEvent(new Event('input')); fixture.detectChanges(); };
  return { fixture, el, store, confirm, q, type, component: fixture.componentInstance };
}

describe('Declarator online players', () => {
  it('lists each connected player with remaining balance and their bet on the current fight', () => {
    const { q, el } = desk();
    const rows = q('online-table').querySelectorAll('tbody tr');
    expect(rows.length).toBe(2);
    expect(q('online-table').querySelector('thead')!.textContent).toContain('Fight #7');
    const ana = el.querySelector('[data-player="ana"]')!;
    expect(ana.textContent).toContain('Ana Cruz');
    expect(ana.querySelector('.side-tag.meron')!.textContent).toBe('MERON');
    expect(ana.textContent).toContain('150');
    expect(ana.querySelector('[data-field="balance"]')!.textContent).toContain('850');
    expect(el.querySelector('[data-player="ben"]')!.textContent).toContain('No bet');
  });
  it('filters the list by username or full name', () => {
    const { el, fixture, component } = desk();
    component.onlineSearch.set('cruz'); fixture.detectChanges();
    expect([...el.querySelectorAll('[data-player]')].map(r => r.getAttribute('data-player'))).toEqual(['ana']);
    component.onlineSearch.set('nobody'); fixture.detectChanges();
    expect(el.textContent).toContain('No online player matches that search.');
  });
});

describe('Declarator desk header', () => {
  it('shows event status, End event, Refresh all and the online player count', () => {
    const { q } = desk();
    expect(q('event-status').textContent!.trim()).toBe('live');
    expect(q('online-players').textContent).toContain('(2)');
    expect(q<HTMLButtonElement>('end-event').disabled).toBe(false);
  });
  it('ends the event only after confirmation', async () => {
    const declined = desk(null);
    await declined.component.endEvent();
    expect(declined.store.api.endEvent).not.toHaveBeenCalled();
    TestBed.resetTestingModule();
    const { component, store, confirm } = desk('');
    await component.endEvent();
    expect(confirm.ask).toHaveBeenCalledWith(expect.objectContaining({ destructive: true, confirmText: 'End event' }));
    expect(store.api.endEvent).toHaveBeenCalledWith('ev');
    expect(store.refresh).toHaveBeenCalled();
  });
  it('refresh all reloads this desk and asks every connected screen to reload', async () => {
    const { component, store } = desk();
    await component.refreshAll();
    expect(store.refresh).toHaveBeenCalled();
    expect(store.api.refreshAll).toHaveBeenCalledWith('ev');
    expect(component.message()).toContain('every connected screen');
  });
});

describe('Declarator fight controls', () => {
  it('jumps to the fight number typed in, digits only', async () => {
    const { q, type, store, component, fixture } = desk();
    type('jump-input', '19a6');
    expect(q<HTMLInputElement>('jump-input').value).toBe('196');
    q<HTMLButtonElement>('set-fight').click();
    await fixture.whenStable();
    expect(store.api.jumpToFight).toHaveBeenCalledWith('ev', 196);
    expect(component.message()).toBe('Next fight set to #196.');
  });
  it('reports a refused jump', async () => {
    const { type, store, component } = desk();
    store.api.jumpToFight.mockReturnValue(throwError(() => new ApiError(409, 'fight_in_progress', 'Fight #7 is in progress.')) as never);
    type('jump-input', '9');
    await component.jump();
    expect(component.actionError()).toContain('in progress');
    expect(store.reconcile).toHaveBeenCalled();
  });
});

describe('Declarator auto ghost betting', () => {
  it('starts with the configured amounts and interval, then shows ACTIVE with a stop button', async () => {
    const { q, type, store, component, fixture } = desk();
    expect(q('ghost-status').textContent!.trim()).toBe('INACTIVE');
    type('ghost-meron', '3628'); type('ghost-wala', '3888'); type('ghost-interval', '5');
    await component.startGhost();
    expect(store.api.startGhostAuto).toHaveBeenCalledWith('ev', { meron_amount: '3628.00', wala_amount: '3888.00', interval_seconds: 5 });
    fixture.detectChanges();
    expect(q('ghost-status').textContent!.trim()).toBe('ACTIVE');
    expect(q('ghost-start')).toBeNull();
    expect(q<HTMLInputElement>('ghost-meron').disabled).toBe(true);
    await component.stopGhost();
    expect(store.api.stopGhostAuto).toHaveBeenCalledWith('ev');
    fixture.detectChanges();
    expect(q('ghost-status').textContent!.trim()).toBe('INACTIVE');
  });
  it('refuses empty amounts or an out-of-range interval without calling the API', async () => {
    const { type, store, component } = desk();
    type('ghost-meron', '0'); type('ghost-wala', '');
    await component.startGhost();
    expect(component.actionError()).toContain('above zero');
    type('ghost-meron', '100'); type('ghost-interval', '0');
    await component.startGhost();
    expect(component.actionError()).toContain('1 to 3600');
    expect(store.api.startGhostAuto).not.toHaveBeenCalled();
  });
});

describe('Declarator winning history beads', () => {
  it('draws a full grid of empty beads and colours official results', () => {
    const fixture = TestBed.createComponent(HistoryBeadsComponent);
    fixture.componentRef.setInput('matches', [
      { id: 'a', match_number: 190, status: 'completed', result: { outcome: 'meron', revision: 1 } },
      { id: 'b', match_number: 191, status: 'completed', result: { outcome: 'wala', revision: 1 } },
      { id: 'c', match_number: 192, status: 'completed', result: { outcome: 'wala', revision: 1 } },
      { id: 'd', match_number: 193, status: 'completed', result: { outcome: 'draw', revision: 1 } },
      { id: 'e', match_number: 194, status: 'cancelled', result: null },
      { id: 'f', match_number: 195, status: 'in_progress', result: null },
    ] as never);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelectorAll('.bead').length).toBe(MIN_BEAD_COLUMNS * 6);
    const kind = (n: number) => el.querySelector(`[data-fight="${n}"]`)!.className;
    expect(kind(190)).toContain('meron'); expect(kind(191)).toContain('wala'); expect(kind(193)).toContain('draw'); expect(kind(194)).toContain('void');
    expect(el.querySelector('[data-fight="195"]')).toBeNull();
    // 191 and 192 are one Wala streak: same column, next row.
    const cell = (n: number) => el.querySelector(`[data-fight="${n}"]`) as HTMLElement;
    expect(cell(192).style.gridColumn).toBe(cell(191).style.gridColumn);
  });
});

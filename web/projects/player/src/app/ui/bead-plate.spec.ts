import { TestBed } from '@angular/core/testing';
import type { Match, MatchOutcome } from '@shared/models';
import { BeadPlateComponent, stakeMark, toBeadPlate } from './bead-plate';

const fight = (n: number, outcome: MatchOutcome | null = null, extra: Partial<Match> = {}): Match => ({
  id: `match-${n}`, event_id: 'event-1', event_name: 'Event', match_number: n,
  meron_name: 'Entry name', wala_name: 'Other entry', meron_details: null, wala_details: null,
  scheduled_at: null, started_at: null, ended_at: null, cancel_reason: null,
  status: outcome ? 'completed' : 'in_progress',
  result: outcome ? { outcome, revision: 1, declared_at: '2026-01-01T00:00:00Z', notes: null } : null,
  ...extra,
});

describe('toBeadPlate', () => {
  it('places decided fights in fight order, top to bottom, seven per column', () => {
    const matches = [fight(9, 'wala'), ...Array.from({ length: 8 }, (_, i) => fight(i + 1, 'meron')), fight(10)];
    const plate = toBeadPlate(matches);
    expect(plate.map((b) => b.match.match_number)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]); // the live fight is not a result
    expect(plate.map((b) => [b.column, b.row]).slice(5)).toEqual([[0, 5], [0, 6], [1, 0], [1, 1]]);
  });

  it('colours by official outcome; cancelled and no contest are neutral', () => {
    const plate = toBeadPlate([
      fight(1, 'meron'), fight(2, 'wala'), fight(3, 'draw'), fight(4, 'no_contest'),
      fight(5, null, { status: 'cancelled' }),
    ]);
    expect(plate.map((b) => b.kind)).toEqual(['meron', 'wala', 'draw', 'void', 'void']);
  });
});

describe('stakeMark', () => {
  it('marks the higher stake L and the lower stake D, and leaves a tie unmarked', () => {
    expect(stakeMark('500.00', '120.00', 'meron')).toBe('L');
    expect(stakeMark('500.00', '120.00', 'wala')).toBe('D');
    expect(stakeMark('80.00', '240.50', 'wala')).toBe('L');
    expect(stakeMark('80.00', '240.50', 'meron')).toBe('D');
    expect(stakeMark('100.00', '100.00', 'meron')).toBeNull();
    expect(stakeMark('nope', '10.00', 'wala')).toBeNull();
  });
});

describe('BeadPlateComponent', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('renders numbered beads that open their fight, and an empty state', () => {
    const fixture = TestBed.createComponent(BeadPlateComponent);
    fixture.componentRef.setInput('matches', [fight(1, 'meron'), fight(2, 'wala', { result: { outcome: 'wala', revision: 2, declared_at: '2026-01-01T00:00:00Z', notes: null } })]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const beads = Array.from(el.querySelectorAll<HTMLButtonElement>('.bead'));
    expect(beads.map((b) => b.textContent!.trim())).toEqual(['1', '2']);
    expect(beads[1].classList).toContain('corrected');
    expect(beads[1].getAttribute('aria-label')).toContain('(corrected)');
    const opened = vi.fn();
    fixture.componentInstance.selected.subscribe(opened);
    beads[0].click();
    expect(opened).toHaveBeenCalledWith(expect.objectContaining({ id: 'match-1' }));

    fixture.componentRef.setInput('matches', []);
    fixture.detectChanges();
    expect(el.textContent).toContain('No official results yet.');
  });
});

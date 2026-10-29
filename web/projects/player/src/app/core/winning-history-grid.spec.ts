import { WinningHistoryOutcome, WinningHistoryResult, toWinningHistoryGrid } from './winning-history-grid';

const records = (...outcomes: WinningHistoryOutcome[]): WinningHistoryResult[] =>
  outcomes.map((result, index) => ({ fightNumber: 101 + index, result }));
const positions = (items: WinningHistoryResult[]) => toWinningHistoryGrid(items).map(({ fightNumber, column, row }) => ({ fightNumber, column, row }));

describe('toWinningHistoryGrid', () => {
  it.each(['MERON', 'WALA'] as const)('places four consecutive %s wins vertically', side => {
    expect(positions(records(side, side, side, side))).toEqual([
      { fightNumber: 101, column: 0, row: 0 }, { fightNumber: 102, column: 0, row: 1 },
      { fightNumber: 103, column: 0, row: 2 }, { fightNumber: 104, column: 0, row: 3 },
    ]);
  });

  it('cuts the streak immediately when the winner changes, including a return to Meron', () => {
    expect(positions(records('MERON', 'MERON', 'MERON', 'MERON', 'WALA', 'WALA', 'MERON'))).toEqual([
      { fightNumber: 101, column: 0, row: 0 }, { fightNumber: 102, column: 0, row: 1 },
      { fightNumber: 103, column: 0, row: 2 }, { fightNumber: 104, column: 0, row: 3 },
      { fightNumber: 105, column: 1, row: 0 }, { fightNumber: 106, column: 1, row: 1 },
      { fightNumber: 107, column: 2, row: 0 },
    ]);
  });

  it('uses a new top-row column for each alternating winner', () => {
    const grid = toWinningHistoryGrid(records('MERON', 'WALA', 'MERON', 'WALA'));
    expect(grid.map(cell => [cell.column, cell.row])).toEqual([[0, 0], [1, 0], [2, 0], [3, 0]]);
  });

  it.each(['MERON', 'WALA'] as const)('wraps a long %s run at six rows and preserves its relationship', side => {
    const grid = toWinningHistoryGrid(records(...Array<WinningHistoryOutcome>(14).fill(side), side === 'MERON' ? 'WALA' : 'MERON'));
    expect(grid.map(cell => [cell.column, cell.row])).toEqual([
      [0, 0], [0, 1], [0, 2], [0, 3], [0, 4], [0, 5],
      [1, 0], [1, 1], [1, 2], [1, 3], [1, 4], [1, 5], [2, 0], [2, 1], [3, 0],
    ]);
    expect(grid.filter(cell => cell.continuation).map(cell => cell.fightNumber)).toEqual([107, 113]);
    expect(new Set(grid.slice(0, 14).map(cell => cell.streakId)).size).toBe(1);
    expect(grid[14].streakId).not.toBe(grid[13].streakId);
  });

  it('keeps draws and cancellations visible in neutral columns and ends the winning run', () => {
    const grid = toWinningHistoryGrid(records('MERON', 'DRAW', 'MERON', 'CANCELLED', 'WALA', 'WALA', 'DRAW', 'DRAW'));
    expect(grid.map(cell => [cell.result, cell.column, cell.row])).toEqual([
      ['MERON', 0, 0], ['DRAW', 1, 0], ['MERON', 2, 0], ['CANCELLED', 3, 0],
      ['WALA', 4, 0], ['WALA', 4, 1], ['DRAW', 5, 0], ['DRAW', 6, 0],
    ]);
  });

  it('deduplicates fights and uses the highest recorded correction, regardless of arrival order', () => {
    const original = { fightNumber: 101, result: 'MERON', revision: 1 } as const;
    const corrected = { ...original, result: 'WALA', revision: 2 } as const;
    expect(toWinningHistoryGrid([corrected, original, corrected])).toMatchObject([{ fightNumber: 101, result: 'WALA', column: 0, row: 0 }]);
  });

  it('does not invent missing fights or infer a consecutive run across a gap', () => {
    const grid = toWinningHistoryGrid([{ fightNumber: 101, result: 'MERON' }, { fightNumber: 103, result: 'MERON' }]);
    expect(grid.map(cell => [cell.fightNumber, cell.column, cell.row])).toEqual([[101, 0, 0], [103, 1, 0]]);
  });

  it('rejects missing/invalid fight numbers, outcomes and revisions at runtime', () => {
    const invalid = [
      { result: 'MERON' }, { fightNumber: 0, result: 'MERON' }, { fightNumber: NaN, result: 'WALA' },
      { fightNumber: 1.5, result: 'MERON' }, { fightNumber: 4, result: 'unofficial' },
      { fightNumber: 5, result: 'WALA', revision: 0 },
    ] as WinningHistoryResult[];
    expect(toWinningHistoryGrid(invalid)).toEqual([]);
  });

  it('returns no cells for an empty history', () => expect(toWinningHistoryGrid([])).toEqual([]));

  it('sorts a copy, never mutates history, and never assigns two results to one cell', () => {
    const history = Object.freeze(records('MERON', 'MERON', 'WALA', 'DRAW', 'WALA').reverse().map(item => Object.freeze(item)));
    const before = JSON.stringify(history);
    const grid = toWinningHistoryGrid(history);
    expect(JSON.stringify(history)).toBe(before);
    expect(grid.map(cell => cell.fightNumber)).toEqual([101, 102, 103, 104, 105]);
    expect(new Set(grid.map(cell => `${cell.column}:${cell.row}`)).size).toBe(grid.length);
    expect(grid.every(cell => cell.row >= 0 && cell.row < 6)).toBe(true);
  });
});

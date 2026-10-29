/** Only finalized outcomes belong in winning history. No missing result is inferred. */
export type WinningHistoryOutcome = 'MERON' | 'WALA' | 'DRAW' | 'CANCELLED';

export interface WinningHistoryResult {
  fightNumber: number;
  result: WinningHistoryOutcome;
  /** Higher official revisions replace older copies of the same fight. */
  revision?: number;
}

export type WinningHistoryCell<T extends WinningHistoryResult = WinningHistoryResult> = T & {
  column: number;
  row: number;
  /** Stable across overflow columns belonging to the same winning run. */
  streakId: number;
  continuation: boolean;
};

export const WINNING_HISTORY_ROWS = 6;
const OUTCOMES = new Set<unknown>(['MERON', 'WALA', 'DRAW', 'CANCELLED']);

/**
 * Returns chronological, non-overlapping coordinates without changing the input.
 *
 * Red/blue runs descend until the winner changes or six rows are occupied. A
 * longer run wraps to the top of the next column, retaining its streakId. Each
 * neutral outcome occupies its own column and ends the winning run. Missing
 * fight numbers also break continuity; no record is generated to fill a gap.
 * Duplicate fights retain the highest official revision (first copy on a tie).
 */
export function toWinningHistoryGrid<T extends WinningHistoryResult>(
  results: readonly T[],
): WinningHistoryCell<T>[] {
  const unique = new Map<number, T>();
  for (const item of results) {
    if (!item || !Number.isSafeInteger(item.fightNumber) || item.fightNumber < 1 || !OUTCOMES.has(item.result)) continue;
    if (item.revision !== undefined && (!Number.isSafeInteger(item.revision) || item.revision < 1)) continue;
    const previous = unique.get(item.fightNumber);
    if (!previous || (item.revision ?? 1) > (previous.revision ?? 1)) unique.set(item.fightNumber, item);
  }

  const ordered = [...unique.values()].sort((a, b) => a.fightNumber - b.fightNumber);
  const cells: WinningHistoryCell<T>[] = [];
  let column = -1;
  let row = 0;
  let streakId = -1;
  let previous: T | undefined;

  for (const item of ordered) {
    const winner = item.result === 'MERON' || item.result === 'WALA';
    const sameRun = winner && previous?.result === item.result && previous.fightNumber + 1 === item.fightNumber;
    const continuation = sameRun && row === WINNING_HISTORY_ROWS - 1;
    if (!sameRun || continuation) {
      column++;
      row = 0;
      if (!sameRun) streakId++;
    } else {
      row++;
    }
    cells.push({ ...item, column, row, streakId, continuation });
    previous = item;
  }
  return cells;
}

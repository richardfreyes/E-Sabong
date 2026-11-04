import { ArenaPointsPipe, MoneyPipe, formatArenaPoints } from './index';

describe('Balance formatting', () => {
  it('formats the play balance as a plain amount: no currency symbol, code or unit', () => {
    expect(formatArenaPoints(123450)).toBe('1,234.50');
    expect(formatArenaPoints(0)).toBe('0.00');
    const pipe = new ArenaPointsPipe();
    expect(pipe.transform('7111.06')).toBe('7,111.06');
    expect(pipe.transform(null)).toBe('—'); // unavailable, never an invented zero
    for (const text of [pipe.transform('1686695.46'), pipe.transform('5')]) {
      expect(text).not.toMatch(/₱|PHP|\$|peso|\bcr\b|\bpts\b|credits|arena points/i);
    }
  });

  it('keeps the peso for real subscription billing only', () => {
    expect(new MoneyPipe().transform(125000)).toBe('₱1,250.00');
  });
});

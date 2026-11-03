import { ArenaPointsPipe, MoneyPipe, formatArenaPoints } from './index';

describe('Arena Points formatting', () => {
  it('formats Arena Points without any currency symbol, code or legacy unit', () => {
    expect(formatArenaPoints(123450)).toBe('1,234.50');
    expect(formatArenaPoints(123450, true)).toBe('1,234.50 Arena Points');
    expect(formatArenaPoints(0)).toBe('0.00');
    const pipe = new ArenaPointsPipe();
    expect(pipe.transform('7111.06')).toBe('7,111.06');
    expect(pipe.transform('7111.06', true)).toBe('7,111.06 Arena Points');
    expect(pipe.transform(null)).toBe('—'); // unavailable, never an invented zero
    for (const text of [pipe.transform('1686695.46'), pipe.transform('5', true)]) {
      expect(text).not.toMatch(/₱|PHP|\$|peso|\bcr\b|\bpts\b|credits/i);
    }
  });

  it('keeps the peso for real subscription billing only', () => {
    expect(new MoneyPipe().transform(125000)).toBe('₱1,250.00');
  });
});

import { currencySymbol, formatMoney, toCents } from './currency';

describe('toCents + formatMoney', () => {
  it('converts API decimal strings exactly and formats with the platform currency', () => {
    expect(toCents('7111.06')).toBe(711106);
    expect(toCents('0.1')).toBe(10);
    expect(toCents('100')).toBe(10000);
    expect(toCents('-55.26')).toBe(-5526);
    expect(toCents(12.345)).toBe(1235);
    expect(toCents('abc')).toBeNull();
    expect(toCents('')).toBeNull();
    expect(formatMoney(toCents('1686695.46')!)).toBe('₱1,686,695.46');
    expect(currencySymbol()).toBe('₱');
  });
});

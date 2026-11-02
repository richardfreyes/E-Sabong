import { CreditsPipe, MoneyPipe, formatCredits } from './index';

describe('play credits formatting', () => {
  it('formats play credits without any currency symbol or code', () => {
    expect(formatCredits(123450)).toBe('1,234.50');
    expect(formatCredits(123450, true)).toBe('1,234.50 credits');
    expect(formatCredits(100, true)).toBe('1.00 credit');
    expect(formatCredits(0)).toBe('0.00');
    const pipe = new CreditsPipe();
    expect(pipe.transform('7111.06')).toBe('7,111.06');
    expect(pipe.transform('7111.06', true)).toBe('7,111.06 credits');
    expect(pipe.transform(null)).toBe('—');
    for (const text of [pipe.transform('1686695.46'), pipe.transform('5', true)]) {
      expect(text).not.toMatch(/₱|PHP|\$|peso/i);
    }
  });

  it('keeps the peso for real subscription billing only', () => {
    expect(new MoneyPipe().transform(125000)).toBe('₱1,250.00');
  });
});

import { LabelPipe, MoneyPipe, PointsPipe, RelativeTimePipe } from './index';

describe('PointsPipe', () => {
  const pipe = new PointsPipe();

  it('formats thousands and handles empty values', () => {
    expect(pipe.transform(12500)).toBe((12500).toLocaleString());
    expect(pipe.transform(null)).toBe('—');
  });

  it('adds explicit signs for ledger deltas', () => {
    expect(pipe.transform(25, true)).toBe('+25');
    expect(pipe.transform(-25, true)).toBe('−25');
    expect(pipe.transform(0, true)).toBe('0');
  });
});

describe('MoneyPipe', () => {
  const pipe = new MoneyPipe();

  it('defaults to Philippine pesos with separators and two decimals', () => {
    expect(pipe.transform(500000)).toBe('₱5,000.00');
    expect(pipe.transform(10000000, 'PHP')).toBe('₱100,000.00');
    expect(pipe.transform(150)).toBe('₱1.50');
    expect(pipe.transform(null)).toBe('—');
  });

  it('labels legacy non-peso amounts by ISO code without converting them', () => {
    const text = pipe.transform(14900, 'USD');
    expect(text).toMatch(/^USD\s149\.00$/);
    expect(text).not.toContain('₱');
  });
});

describe('LabelPipe', () => {
  it('humanises snake case', () => {
    expect(new LabelPipe().transform('master_agent')).toBe('Master agent');
    expect(new LabelPipe().transform(null)).toBe('');
  });
});

describe('RelativeTimePipe', () => {
  it('describes past timestamps', () => {
    const pipe = new RelativeTimePipe();
    expect(pipe.transform(null)).toBe('never');
    expect(pipe.transform(new Date(Date.now() - 2 * 3600_000).toISOString())).toMatch(/2 hours ago/);
    expect(pipe.transform(new Date().toISOString())).toBe('just now');
  });
});

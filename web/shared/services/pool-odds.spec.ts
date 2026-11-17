import vectors from './pool-odds.vectors.json';
import { poolBoardOdds, exactCents } from './pool-odds';

describe('pool board odds (same formula as the API)', () => {
  it('matches every case generated from api/app/modules/betting/odds.py', () => {
    expect(vectors.cases.length).toBeGreaterThan(50);
    for (const c of vectors.cases) {
      expect({ case: c, ...poolBoardOdds(c.meron_cents, c.wala_cents) }).toEqual({ case: c, meron: c.meron, wala: c.wala });
    }
  });

  it('opens at 1.83, moves both sides together and never quotes below 1', () => {
    expect(poolBoardOdds(0, 0)).toEqual({ meron: '1.83', wala: '1.83' });
    expect(poolBoardOdds(500_000, 0)).toEqual({ meron: '1.83', wala: '1.83' });
    // +5K Meron, then +10K Wala
    expect(poolBoardOdds(500_000, 1_000_000)).toEqual({ meron: '2.44', wala: '1.22' });
    expect(poolBoardOdds(10n ** 15n, 1n)).toEqual({ meron: '1.00', wala: '2.66' });
  });

  it('reads decimal strings as exact cents', () => {
    expect(exactCents('5000.00')).toBe(500_000n);
    expect(exactCents('0.1')).toBe(10n);
    expect(exactCents('12345678901234.56')).toBe(1_234_567_890_123_456n);
    expect(exactCents(10_000)).toBe(1_000_000n);
    expect(exactCents(null)).toBe(0n);
    expect(exactCents('bad')).toBe(0n);
  });
});

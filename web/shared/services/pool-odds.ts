/**
 * The MERON/WALA pool price, exactly as the API computes it (`pool_odds` + `display_odds` in
 * api/app/modules/betting/odds.py), for showing a projected price before the server confirms it.
 *
 *   meron = 3.66 × wala_pool / (meron_pool + wala_pool)
 *   wala  = 3.66 × meron_pool / (meron_pool + wala_pool)
 *
 * Both sides are 1.83 until each pool has points. A price below 1 is quoted at 1 and the other side
 * keeps 2.66. Each price is shown at 2 decimal places, half up. The commission does not move it.
 * Pools are whole cents and the arithmetic is integer (BigInt), so no float rounding can differ from
 * the API's Decimal result. `pool-odds.vectors.json` holds cases generated from the Python module;
 * both the API tests and this module's spec check them.
 *
 * This is the pool price only. A Declarator's manual pair or Owner-configured odds do not move with
 * the pools (the book's `odds.source` says which applies), and a projection is never a settlement
 * value: MERON/WALA are paid at the board price when betting closes.
 */
export interface BoardOdds { meron: string; wala: string }

const SUM = 366n; // 3.66 in hundredths
const OPEN = '1.83';
const MIN = 100n; // 1.00 in hundredths

/** Hundredths (an integer) as a 2 dp decimal string: 183n -> "1.83". */
function hundredths(value: bigint): string {
  const whole = value / 100n, frac = value % 100n;
  return `${whole}.${frac.toString().padStart(2, '0')}`;
}

/** round_half_up(366 × part / total), in hundredths of a price. */
function priceHundredths(part: bigint, total: bigint): bigint {
  return (2n * SUM * part + total) / (2n * total);
}

/** The board price for pools given in whole cents. */
export function poolBoardOdds(meronCents: number | bigint, walaCents: number | bigint): BoardOdds {
  const meron = BigInt(meronCents), wala = BigInt(walaCents);
  if (meron <= 0n || wala <= 0n) return { meron: OPEN, wala: OPEN };
  const total = meron + wala;
  // Unrounded meron < 1  <=>  366 × wala < 100 × total (and likewise for wala).
  if (SUM * wala < MIN * total) return { meron: hundredths(MIN), wala: hundredths(SUM - MIN) };
  if (SUM * meron < MIN * total) return { meron: hundredths(SUM - MIN), wala: hundredths(MIN) };
  return { meron: hundredths(priceHundredths(wala, total)), wala: hundredths(priceHundredths(meron, total)) };
}

/** A decimal string of points ("5000.00") as whole cents, without float rounding. */
export function exactCents(value: string | number | null | undefined): bigint {
  if (value === null || value === undefined || value === '') return 0n;
  const text = typeof value === 'number' ? value.toFixed(2) : value.trim();
  const match = /^(-?)(\d*)(?:\.(\d*))?$/.exec(text);
  if (!match) return 0n;
  const [, sign, whole, frac = ''] = match;
  const cents = BigInt(whole || '0') * 100n + BigInt((frac + '00').slice(0, 2));
  return sign ? -cents : cents;
}

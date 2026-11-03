/**
 * Arena Points: the one non-monetary gameplay balance (simulated betting). They have no cash value,
 * cannot be bought, transferred, withdrawn or redeemed, and are never shown with a currency symbol
 * or code. Philippine Peso formatting (formatMoney, MoneyPipe) is reserved for subscription billing.
 * The retired transferable "points" module is a separate, read-only admin archive (PointsPipe).
 */
const formatter = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const ARENA_POINTS_UNIT = 'Arena Points';

/** Integer hundredths -> "1,234.50", or "1,234.50 Arena Points" with the unit. */
export function formatArenaPoints(hundredths: number, withUnit = false): string {
  const text = formatter.format(hundredths / 100);
  return withUnit ? `${text} ${ARENA_POINTS_UNIT}` : text;
}

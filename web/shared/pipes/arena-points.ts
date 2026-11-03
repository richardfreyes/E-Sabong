/**
 * The play balance (shown to users as "Balance"): the one non-monetary gameplay balance (simulated
 * betting). It has no cash value, cannot be bought, transferred, withdrawn or redeemed, and is never
 * shown with a currency symbol or code. Philippine Peso formatting (formatMoney, MoneyPipe) is
 * reserved for subscription billing. The retired transferable "points" module is a separate,
 * read-only admin archive (PointsPipe). Code identifiers keep the original "arena points" name.
 */
const formatter = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Integer hundredths -> "1,234.50": a plain amount with no currency and no unit. */
export function formatArenaPoints(hundredths: number): string {
  return formatter.format(hundredths / 100);
}

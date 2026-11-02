/**
 * Play credits: the non-monetary units used by simulated betting. They have no cash value and
 * are never shown with a currency symbol or code; Philippine Peso formatting (formatMoney,
 * MoneyPipe) is reserved for real subscription billing.
 */
const formatter = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const CREDITS_UNIT = 'credits';
export const CREDITS_SHORT = 'cr';

/** Integer hundredths -> "1,234.50", or "1,234.50 credits" with the unit. */
export function formatCredits(hundredths: number, withUnit = false): string {
  const text = formatter.format(hundredths / 100);
  return withUnit ? `${text} ${hundredths === 100 ? 'credit' : CREDITS_UNIT}` : text;
}

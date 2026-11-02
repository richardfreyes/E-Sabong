/**
 * Platform currency configuration — the single place to add a currency.
 * Must match SUPPORTED_CURRENCIES in api/app/core/config.py.
 */
export interface CurrencyConfig {
  code: string;
  name: string;
  symbol: string;
  locale: string;
}

export const CURRENCIES: Readonly<Record<string, CurrencyConfig>> = {
  PHP: { code: 'PHP', name: 'Philippine Peso', symbol: '₱', locale: 'en-PH' },
};

export const DEFAULT_CURRENCY = 'PHP';
export const SUPPORTED_CURRENCIES = Object.keys(CURRENCIES);

const formatters = new Map<string, Intl.NumberFormat>();

function formatter(currency: string): Intl.NumberFormat {
  let fmt = formatters.get(currency);
  if (!fmt) {
    // Legacy records in other currencies show their ISO code ("USD 49.00")
    // so they can never be mistaken for pesos.
    fmt = new Intl.NumberFormat(CURRENCIES[DEFAULT_CURRENCY].locale, {
      style: 'currency',
      currency,
      currencyDisplay: currency === DEFAULT_CURRENCY ? 'symbol' : 'code',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    formatters.set(currency, fmt);
  }
  return fmt;
}

/** Integer cents -> "₱1,500.00". */
export function formatMoney(cents: number, currency: string = DEFAULT_CURRENCY): string {
  return formatter(currency || DEFAULT_CURRENCY).format(cents / 100);
}

/**
 * Decimal amount -> integer cents for formatMoney. API amounts are decimal strings ("1234.50"):
 * those convert exactly, without going through binary floating point. Null if unparsable.
 */
export function toCents(value: number | string): number | null {
  if (typeof value === 'string') {
    const m = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(value.trim());
    if (m) return (m[1] ? -1 : 1) * (Number(m[2]) * 100 + Number((m[3] ?? '').padEnd(2, '0')));
  }
  const n = Number(value);
  return Number.isFinite(n) && value !== '' ? Math.round(n * 100) : null;
}

export function currencySymbol(currency: string = DEFAULT_CURRENCY): string {
  return CURRENCIES[currency]?.symbol ?? currency;
}

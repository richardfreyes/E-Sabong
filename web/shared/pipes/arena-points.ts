const formatter = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Integer hundredths -> "1,234.50": a plain amount with no currency and no unit. */
export function formatArenaPoints(hundredths: number): string {
  return formatter.format(hundredths / 100);
}

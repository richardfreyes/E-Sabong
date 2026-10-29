import { Pipe, PipeTransform } from '@angular/core';

import { DEFAULT_CURRENCY, formatMoney } from './currency';

export * from './currency';

/** 12500 -> "12,500" */
@Pipe({ name: 'points' })
export class PointsPipe implements PipeTransform {
  transform(value: number | null | undefined, signed = false): string {
    if (value === null || value === undefined) return '—';
    const text = Math.abs(value).toLocaleString();
    if (!signed) return value < 0 ? `-${text}` : text;
    return value > 0 ? `+${text}` : value < 0 ? `−${text}` : '0';
  }
}

/** Integer cents -> localised currency string. */
@Pipe({ name: 'money' })
export class MoneyPipe implements PipeTransform {
  transform(cents: number | null | undefined, currency: string = DEFAULT_CURRENCY): string {
    if (cents === null || cents === undefined) return '—';
    return formatMoney(cents, currency);
  }
}

/** "master_agent" -> "Master agent" */
@Pipe({ name: 'label' })
export class LabelPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) return '';
    const text = value.replace(/[_.]/g, ' ');
    return text.charAt(0).toUpperCase() + text.slice(1);
  }
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 31_536_000],
  ['month', 2_592_000],
  ['day', 86_400],
  ['hour', 3_600],
  ['minute', 60],
];

/** ISO timestamp -> "3 hours ago" */
@Pipe({ name: 'relativeTime' })
export class RelativeTimePipe implements PipeTransform {
  private readonly fmt = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

  transform(value: string | null | undefined): string {
    if (!value) return 'never';
    const seconds = (new Date(value).getTime() - Date.now()) / 1000;
    for (const [unit, size] of UNITS) {
      if (Math.abs(seconds) >= size) return this.fmt.format(Math.round(seconds / size), unit);
    }
    return 'just now';
  }
}

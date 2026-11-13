import { Injectable, inject } from '@angular/core';
import { ApiClient } from './api-client';

export interface QuickBetSettings { meron: string[]; wala: string[]; }
export const defaultQuickBets = (): QuickBetSettings => ({
  meron: ['5000.00', '10000.00', '20000.00', '50000.00'],
  wala: ['5000.00', '10000.00', '20000.00', '50000.00'],
});
export function quickBetLabel(amount: string | number): string {
  const value = Number(amount);
  return value >= 1000 && value % 1000 === 0 ? `${value / 1000}K` : value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}
@Injectable({ providedIn: 'root' })
export class QuickBetSettingsApi {
  private readonly api = inject(ApiClient);
  get = () => this.api.get<QuickBetSettings>('/declarator/settings/quick-bets');
  save = (body: QuickBetSettings) => this.api.put<QuickBetSettings>('/declarator/settings/quick-bets', body);
}

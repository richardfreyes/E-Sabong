import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { CellDefDirective, Column, DataTableComponent } from './data-table';

interface Row {
  name: string;
  amount: number;
}

@Component({
  imports: [DataTableComponent, CellDefDirective],
  template: `
    <app-data-table [columns]="columns" [rows]="rows()" [total]="rows().length" [loading]="loading()" [error]="error()" [filters]="filters()"
                    (search)="searched.push($event)">
      <ng-template appCell="name" let-row><strong class="custom">{{ row.name }}</strong></ng-template>
    </app-data-table>
  `,
})
class Host {
  columns: Column<Row>[] = [
    { key: 'name', label: 'Name' },
    { key: 'amount', label: 'Amount', align: 'end', cell: (r) => r.amount.toFixed(2) },
  ];
  rows = signal<Row[]>([]);
  loading = signal(false);
  error = signal<string | null>(null);
  searched: string[] = [];
  filters = signal(false);
}

describe('DataTableComponent', () => {
  async function render() {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    return fixture;
  }

  it('renders rows with custom cell templates and formatters', async () => {
    const fixture = await render();
    fixture.componentInstance.rows.set([{ name: 'alpha', amount: 3 }]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('strong.custom')?.textContent).toBe('alpha');
    expect(el.querySelector('td.align-end')?.textContent?.trim()).toBe('3.00');
  });

  it('shows empty, loading and error states', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Nothing here yet');
    fixture.componentInstance.loading.set(true);
    await fixture.whenStable();
    expect(el.querySelector('[aria-busy="true"]')).not.toBeNull();
    fixture.componentInstance.loading.set(false);
    fixture.componentInstance.error.set('Boom');
    await fixture.whenStable();
    expect(el.querySelector('[role="alert"]')?.textContent).toContain('Boom');
  });

  it('debounces search input', async () => {
    const fixture = await render();
    vi.useFakeTimers();
    const input = (fixture.nativeElement as HTMLElement).querySelector('input[type="search"]') as HTMLInputElement;
    for (const v of ['a', 'al', 'alp']) {
      input.value = v;
      input.dispatchEvent(new Event('input'));
    }
    vi.advanceTimersByTime(350);
    expect(fixture.componentInstance.searched).toEqual(['alp']);
    vi.useRealTimers();
  });

  it('clears a search through the same debounced output used by the API list', async () => {
    const fixture = await render();
    vi.useFakeTimers();
    try {
      const input = fixture.nativeElement.querySelector('input[type="search"]') as HTMLInputElement;
      input.value = 'alpha';
      input.dispatchEvent(new Event('input'));
      vi.advanceTimersByTime(350);
      fixture.detectChanges();
      const clear = fixture.nativeElement.querySelector('button[aria-label="Clear search"]') as HTMLButtonElement;
      clear.click();
      vi.advanceTimersByTime(350);
      fixture.detectChanges();
      expect(fixture.componentInstance.searched).toEqual(['alpha', '']);
      expect(input.value).toBe('');
    } finally { vi.useRealTimers(); }
  });

  it('makes the mobile filter disclosure keyboard-accessible without discarding filter content', async () => {
    const fixture = await render();
    fixture.componentInstance.filters.set(true);
    fixture.detectChanges();
    const toggle = fixture.nativeElement.querySelector('.filter-toggle') as HTMLButtonElement;
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    toggle.click();
    fixture.detectChanges();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const filters = fixture.nativeElement.querySelector('.table-filters');
    expect(filters?.classList.contains('open')).toBe(true);
  });

});

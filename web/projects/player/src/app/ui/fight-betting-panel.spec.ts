import { TestBed } from '@angular/core/testing';

import type { BetLimits, BetPosition, FightBook } from '@shared/models';
import { FightBettingPanelComponent } from './fight-betting-panel';
import { COUNT_UP_MS } from './count-up';

const BOOK: FightBook = {
  match_id: 'm1', state: 'open', accepting: true,
  pools: { meron: '7111.06', wala: '9107.19', draw: '972.00' },
  odds: { meron: '2.0526454', wala: '1.6027424', meron_display: '2.05', wala_display: '1.60' },
  final: false, draw_multiplier: '8.00', commission_rate: '0.1000', bet_count: 40, outcome: null, version: 7,
};
const MINE: BetPosition = {
  meron: { stake: '100.00', pays: '205.26', result: null },
  wala: { stake: '0.00', pays: '0.00', result: null },
  draw: { stake: '50.00', pays: '400.00', result: null },
  settled_payout: null, settled_net: null,
};
const LIMITS: BetLimits = { min_bet: '10.00', max_bet: '10000.00', max_fight_stake: '50000.00', remaining_fight_stake: '49850.00' };

describe('FightBettingPanelComponent', () => {
  function setup(overrides: Record<string, unknown> = {}) {
    const fixture = TestBed.createComponent(FightBettingPanelComponent);
    for (const [k, v] of Object.entries({ book: BOOK, mine: MINE, limits: LIMITS, balance: '10300.35', ...overrides })) fixture.componentRef.setInput(k, v);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const emitted = vi.fn();
    fixture.componentInstance.bet.subscribe(emitted);
    const field = (side: string, name: string) => el.querySelector(`.bet.${side} [data-field="${name}"]`)!.textContent!.replace(/\s+/g, ' ').trim();
    const text = () => el.textContent!.replace(/\s+/g, ' ');
    const button = (name: RegExp) => Array.from(el.querySelectorAll('button')).find((b) => name.test(b.getAttribute('aria-label') ?? b.textContent!)) as HTMLButtonElement;
    const card = (side: string) => el.querySelector(`.bet.${side}`) as HTMLButtonElement;
    const input = el.querySelector('input') as HTMLInputElement;
    const type = (value: string) => {
      input.value = value;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    };
    return { fixture, el, emitted, field, text, button, card, input, type };
  }

  it('shows all-player pools and server multipliers apart from the player BET and PAYS', () => {
    const { field, text, el } = setup();
    expect(field('meron', 'pool')).toBe('7,111.06');
    expect(field('wala', 'pool')).toBe('9,107.19');
    expect(field('meron', 'rate')).toBe('2.05×');
    expect(field('wala', 'rate')).toBe('1.60×');
    expect(field('meron', 'bet')).toBe('100.00');
    expect(field('meron', 'pays')).toBe('205.26');
    expect(field('wala', 'bet')).toBe('0.00');
    expect(field('wala', 'pays')).toBe('0.00');
    // 7111.06 / (7111.06 + 9107.19)
    expect(el.querySelector('[data-field="draw-rate"]')!.textContent).toBe('x8');
    expect(el.querySelector('[data-field="draw-pool"]')!.textContent).toBe('972.00');
    expect(el.querySelector('[data-field="draw-bet"]')!.textContent).toBe('50.00');
    expect(el.querySelector('[data-field="draw-pays"]')!.textContent).toBe('400.00');
    expect(el.querySelector('[data-field="balance"]')!.textContent).toBe('10,300.35');
    expect(text()).toContain('Betting Open');
    expect(text()).toContain('Min 10.00 · Max 10,000.00 · 49,850.00 left this fight');
  });

  it('never invents odds: dashes while a side has no stake', () => {
    const empty = { meron: '0.00', wala: '0.00', draw: '0.00' };
    const mine: BetPosition = { ...MINE, meron: { stake: '0.00', pays: null, result: null } };
    const { field, text, el } = setup({ mine, book: { ...BOOK, pools: empty, odds: { meron: null, wala: null, meron_display: null, wala_display: null } } });
    expect(field('meron', 'rate')).toBe('—');
    expect(field('wala', 'rate')).toBe('—');
    expect(field('meron', 'pays')).toBe('—');
  });

  it('bets the amount on each side and labels the buttons with their figures', () => {
    const { card, emitted, el, fixture } = setup();
    (el.querySelector('[aria-label="500.00"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(el.querySelector('[aria-label="500.00"]')!.getAttribute('aria-pressed')).toBe('true');
    expect(card('meron').getAttribute('aria-label')).toBe(
      'Bet 500.00 on MERON. All players 7,111.06, odds 2.05×, your bet 100.00, returns 205.26',
    );
    card('meron').click();
    card('wala').click();
    (el.querySelector('button.draw') as HTMLButtonElement).click();
    expect(emitted.mock.calls.map((c) => c[0])).toEqual([
      { side: 'meron', amount: '500.00' }, { side: 'wala', amount: '500.00' }, { side: 'draw', amount: '500.00' },
    ]);
  });

  it('accepts cents, and rejects malformed, out-of-limit and over-balance amounts', () => {
    const { type, input, card, emitted, el } = setup();
    type('12.345abc');
    expect(input.value).toBe('12.34'); // cleaned as typed: two decimals at most
    card('meron').click();
    expect(emitted).toHaveBeenCalledWith({ side: 'meron', amount: '12.34' });
    for (const [raw, message] of [['', 'Enter a bet amount'], ['5', 'minimum'], ['10000.01', 'maximum'], ['.', 'valid amount']]) {
      type(raw);
      expect(el.querySelector('#pp-bet-help')!.textContent).toContain(message);
      expect(card('meron').disabled).toBe(true);
    }
    const low = setup({ balance: '50.00' });
    low.type('60');
    expect(low.el.querySelector('#pp-bet-help')!.textContent).toContain('Insufficient balance.');
    expect(low.card('wala').disabled).toBe(true);
  });

  it('All in fills the amount, to the cent, capped by balance, max bet and the fight limit, without betting', () => {
    const { button, input, fixture, emitted } = setup({ balance: '100.35' });
    button(/^All in$/).click();
    fixture.detectChanges();
    expect(input.value).toBe('100.35');
    fixture.componentRef.setInput('balance', '90000.00');
    fixture.detectChanges();
    button(/^All in$/).click();
    fixture.detectChanges();
    expect(input.value).toBe('10000');
    fixture.componentRef.setInput('limits', { ...LIMITS, remaining_fight_stake: '1234.56' });
    fixture.detectChanges();
    button(/^All in$/).click();
    fixture.detectChanges();
    expect(input.value).toBe('1234.56');
    expect(emitted).not.toHaveBeenCalled();
  });

  it('keeps every figure when betting closes and only disables the actions', () => {
    const { card, button, text, field, el } = setup({ book: { ...BOOK, state: 'closed', accepting: false, final: true } });
    expect(text()).toContain('Betting Closed');
    for (const b of [card('meron'), card('wala'), el.querySelector('button.draw') as HTMLButtonElement, button(/^All in$/)]) expect(b.disabled).toBe(true);
    expect(field('meron', 'pool')).toBe('7,111.06');
    expect(field('meron', 'rate')).toBe('2.05×'); // final: no "est"
    expect(field('meron', 'bet')).toBe('100.00');
    expect(field('meron', 'pays')).toBe('205.26');
    expect(el.querySelector('[data-field="balance"]')!.textContent).toBe('10,300.35');
  });

  it('shows the settled result per side and the net', () => {
    const settled: BetPosition = {
      meron: { stake: '100.00', pays: '205.26', result: 'won' }, wala: { stake: '0.00', pays: '0.00', result: null },
      draw: { stake: '50.00', pays: '0.00', result: 'lost' }, settled_payout: '205.26', settled_net: '55.26',
    };
    const { text, field, card } = setup({ mine: settled, book: { ...BOOK, state: 'settled', accepting: false, final: true, outcome: 'meron' } });
    expect(text()).toContain('Settled · MERON');
    expect(field('meron', 'pays')).toBe('205.26Won');
    expect(text()).toContain('Returned 205.26 · Net +55.26');
    expect(card('meron').disabled).toBe(true);
  });

  it('explains a fight that has not opened, and reports errors and progress', () => {
    const idle = setup({ book: { ...BOOK, state: null, accepting: false } });
    expect(idle.text()).toContain('Betting Closed');
    expect(idle.text()).toContain('Betting has not opened for this fight yet');
    const busy = setup({ placing: 'wala', error: 'Betting is closed for this fight' });
    expect(busy.card('wala').textContent).toContain('Placing…');
    expect(busy.card('meron').disabled).toBe(true); // no second bet while one is in flight
    expect(busy.el.querySelector('[role="alert"]')!.textContent).toBe('Betting is closed for this fight');
    const loading = setup({ book: null, loading: true });
    expect(loading.el.querySelector('.skeleton')).not.toBeNull();
  });
});

describe('FightBettingPanelComponent non-monetary wording', () => {
  it('never shows a currency symbol, cash wording or legacy units on the balance', () => {
    TestBed.configureTestingModule({});
    const fixture = TestBed.createComponent(FightBettingPanelComponent);
    for (const [k, v] of Object.entries({ book: BOOK, mine: MINE, limits: LIMITS, balance: '10300.35' })) fixture.componentRef.setInput(k, v);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const visible = el.textContent ?? '';
    const labels = Array.from(el.querySelectorAll('[aria-label],[title]'))
      .map((n) => `${n.getAttribute('aria-label') ?? ''} ${n.getAttribute('title') ?? ''}`).join(' ');
    for (const text of [visible, labels]) {
      expect(text).not.toMatch(/₱|PHP|peso|\$|cash balance|deposit|payout|withdrawal/i);
    }
    expect(visible).toContain('Bet amount');
    expect(visible).toContain('Available Balance');
    expect(visible).not.toMatch(/arena points/i);
    expect(visible).not.toMatch(/credits|\bcr\b|\bpts\b/i);
    expect(visible).toContain('no cash value');
    expect(el.querySelector('.cur')).toBeNull(); // no unit prefix in the amount field
    TestBed.resetTestingModule();
  });
});

describe('FightBettingPanelComponent data integrity and states', () => {
  afterEach(() => TestBed.resetTestingModule());

  function render(inputs: Record<string, unknown>) {
    TestBed.configureTestingModule({});
    const fixture = TestBed.createComponent(FightBettingPanelComponent);
    for (const [k, v] of Object.entries(inputs)) fixture.componentRef.setInput(k, v);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const text = () => el.textContent!.replace(/\s+/g, ' ');
    const value = (selector: string) => el.querySelector(selector)!.textContent!.replace(/\s+/g, ' ').trim();
    return { fixture, el, text, value };
  }

  it('shows an error with retry, never zero figures, when the fight could not be loaded', () => {
    const { fixture, el, text } = render({ book: null, mine: null, error: 'Network error', loading: false, balance: null });
    const retried = vi.fn();
    fixture.componentInstance.retry.subscribe(retried);
    expect(el.querySelector('[role="alert"]')!.textContent).toContain('Betting figures are unavailable');
    expect(text()).toContain('Network error');
    expect(text()).not.toMatch(/0\.00/);
    expect(el.querySelector('.bet')).toBeNull();
    (el.querySelector('button.retry') as HTMLButtonElement).click();
    expect(retried).toHaveBeenCalledOnce();
  });

  it('renders missing figures as dashes and real zeros from the API as zeros', () => {
    const missing = render({ book: BOOK, mine: null, limits: LIMITS, balance: '100.00' });
    expect(missing.value('.bet.meron [data-field="bet"]')).toBe('—');
    expect(missing.value('.bet.meron [data-field="pays"]')).toBe('—');
    expect(missing.value('[data-field="draw-bet"]')).toBe('—');
    expect(missing.value('.bet.meron [data-field="pool"]')).toBe('7,111.06'); // pools came from the server
    TestBed.resetTestingModule();
    const zero = { stake: '0.00', pays: '0.00', result: null };
    const real = render({ book: BOOK, mine: { meron: zero, wala: zero, draw: zero, settled_payout: null, settled_net: null }, limits: LIMITS, balance: '0.00' });
    expect(real.value('.bet.meron [data-field="bet"]')).toBe('0.00');
    expect(real.value('[data-field="balance"]')).toBe('0.00');
    expect(real.text()).toContain('Insufficient balance.');
  });

  it('never invents pools before a book exists', () => {
    const { value, text } = render({ book: null, mine: null, loading: false, error: null, balance: '50.00' });
    expect(value('.bet.meron [data-field="pool"]')).toBe('—');
    expect(value('[data-field="draw-pool"]')).toBe('—');
    expect(text()).toContain('Betting has not opened for this fight yet');
  });

  it('treats an unknown play-credit balance as unavailable, not unlimited', () => {
    const { el, text } = render({ book: BOOK, mine: MINE, limits: LIMITS, balance: null });
    expect(text()).toContain('Your balance could not be loaded');
    expect((el.querySelector('.bet.meron') as HTMLButtonElement).disabled).toBe(true);
  });

  it('marks the sides the player has a stake on, from the API position only', () => {
    const { el } = render({ book: BOOK, mine: MINE, limits: LIMITS, balance: '1000.00' });
    expect(el.querySelector('.bet.meron')!.classList).toContain('mine');
    expect(el.querySelector('.bet.meron .tag')!.textContent).toBe('Your side');
    expect(el.querySelector('.bet.wala')!.classList).not.toContain('mine');
    expect(el.querySelector('.bet.wala .tag')).toBeNull();
    expect(el.querySelector('.bet.draw')!.classList).toContain('mine'); // MINE has a DRAW stake
  });

  it('gives all three selections the same structure: name, pool, odds, bet, returns and a solid action', () => {
    const { el } = render({ book: BOOK, mine: MINE, limits: LIMITS, balance: '1000.00' });
    const cards = Array.from(el.querySelectorAll<HTMLButtonElement>('.cards > button.bet'));
    expect(cards.map((c) => c.classList[1])).toEqual(['meron', 'wala', 'draw']);
    expect(cards.map((c) => c.querySelector('.bar')!.textContent!.trim())).toEqual(['Bet Meron', 'Bet Wala', 'Bet Draw']);
    for (const c of cards) {
      expect(c.querySelectorAll('.head .name, .pool b, .rows .row, .bar').length).toBe(6);
      expect(c.type).toBe('button'); // native buttons: Tab, Enter and Space work
      expect(c.getAttribute('aria-describedby')).toBe('pp-bet-help');
    }
    expect(el.querySelector('.bet.draw [data-field="draw-rate"]')!.textContent).toBe('x8');
  });

  it('explains why betting is unavailable for closed, settled and unopened fights', () => {
    for (const [book, reason] of [
      [{ ...BOOK, state: 'closed', accepting: false }, 'Betting is closed for this fight.'],
      [{ ...BOOK, state: 'settled', accepting: false, outcome: 'meron' }, 'This fight is settled; betting is over.'],
      [{ ...BOOK, state: null, accepting: false }, 'Betting has not opened for this fight.'],
    ] as const) {
      const { el } = render({ book, mine: MINE, limits: LIMITS, balance: '1000.00' });
      expect(el.querySelector('#pp-bet-help')!.textContent).toBe(reason);
      for (const card of Array.from(el.querySelectorAll<HTMLButtonElement>('.bet'))) expect(card.disabled).toBe(true);
      TestBed.resetTestingModule();
    }
  });

  it('cannot submit twice while a bet is being placed, and keeps the typed amount across live updates', async () => {
    const { fixture, el } = render({ book: BOOK, mine: MINE, limits: LIMITS, balance: '1000.00' });
    const emitted = vi.fn();
    fixture.componentInstance.bet.subscribe(emitted);
    const input = el.querySelector('input') as HTMLInputElement;
    input.value = '250';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    (el.querySelector('.bet.wala') as HTMLButtonElement).click();
    fixture.componentRef.setInput('placing', 'wala');
    fixture.detectChanges();
    (el.querySelector('.bet.wala') as HTMLButtonElement).click();
    (el.querySelector('.bet.meron') as HTMLButtonElement).click();
    expect(emitted).toHaveBeenCalledTimes(1);
    expect(el.querySelector('.bet.wala .bar')!.textContent!.trim()).toBe('Placing…');
    expect(el.querySelector('#pp-bet-help')!.textContent).toBe('Placing your bet…');
    // A live frame with new pools arrives; the amount field and its value survive.
    fixture.componentRef.setInput('book', { ...BOOK, pools: { ...BOOK.pools, meron: '9000.00' }, version: BOOK.version + 1 });
    fixture.componentRef.setInput('placing', null);
    fixture.detectChanges();
    expect(el.querySelector('input')).toBe(input);
    expect(input.value).toBe('250');
    // The pool counts up to the new figure, then rests on it.
    await new Promise((resolve) => setTimeout(resolve, COUNT_UP_MS + 150));
    fixture.detectChanges();
    expect(el.querySelector('.bet.meron [data-field="pool"]')!.textContent).toBe('9,000.00');
  });
});

import { defaultQuickBets } from '@shared/services';
import { TestBed } from '@angular/core/testing';
import { DatePipe } from '@angular/common';
import { NO_ERRORS_SCHEMA, signal } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { of, throwError } from 'rxjs';
import { ConfirmService } from '@shared/components';
import { ArenaPointsPipe } from '@shared/pipes';
import { ApiError, BettingControl } from '@shared/services';
import { DeclaratorDashboard, cancellationReason, confirmQuestion, profitPercent } from './dashboard';
import { DeclaratorStore } from './store';

function setup(answer: Promise<string|null> = Promise.resolve('')) {
  const record = {match:{id:'fight-135',match_number:135,status:'in_progress'},event_name:'Event'};
  const store = { quickBets: signal(defaultQuickBets()),current:vi.fn(()=>record),canSubmit:()=>true,api:{submit:vi.fn((_id:string, _body:{request_id:string})=>of({status:'pending_approval'}))},refresh:vi.fn(),reconcile:vi.fn()};
  const confirm = {ask:vi.fn(()=>answer)};
  TestBed.configureTestingModule({providers:[{provide:ConfirmService,useValue:confirm},{provide:MatDialog,useValue:{open:vi.fn()}}]});
  TestBed.overrideComponent(DeclaratorDashboard,{set:{template:'',imports:[],providers:[{provide:DeclaratorStore,useValue:store}]}});
  const component = TestBed.createComponent(DeclaratorDashboard).componentInstance;
  return {component,store,confirm,record};
}

describe('Declarator confirmation and submission',()=>{
  it('cannot submit until the confirmation resolves, and cancel sends no declaration',async()=>{
    let resolve!: (value:string|null)=>void;
    const {component,store}=setup(new Promise(r=>resolve=r));
    const pending = component.declare('meron');
    expect(component.busy()).toBe(true);expect(store.api.submit).not.toHaveBeenCalled();
    resolve(null);await pending;
    expect(store.api.submit).not.toHaveBeenCalled();expect(component.busy()).toBe(false);
  });
  it.each(['meron','wala'] as const)('submits confirmed %s for the captured fight and waits for approval',async outcome=>{
    const {component,store,confirm}=setup();
    await component.declare(outcome);
    expect(confirm.ask).toHaveBeenCalledWith(expect.objectContaining({confirmText:'Confirm declaration',
      message:expect.stringContaining(`Are you sure you want to declare ${outcome.toUpperCase()} as the winner of Fight #135?`)}));
    expect(store.api.submit).toHaveBeenCalledWith('fight-135',expect.objectContaining({outcome,base_revision:0,request_id:expect.any(String)}));
    expect(component.message()).toContain('Awaiting approval');expect(store.refresh).toHaveBeenCalledOnce();
  });
  it('requires a reason to cancel a fight and sends it with the declaration',async()=>{
    const {component,store,confirm}=setup(Promise.resolve('Rooster injured before release'));
    await component.declare('cancelled');
    expect(confirm.ask).toHaveBeenCalledWith(expect.objectContaining({inputLabel:'Cancellation reason',inputRequired:true,destructive:true,
      confirmText:'Cancel fight',cancelText:'Keep fight',message:expect.stringContaining('cancel Fight #135')}));
    expect(store.api.submit).toHaveBeenCalledWith('fight-135',expect.objectContaining({outcome:'cancelled',reason:'Rooster injured before release'}));
  });
  it('keeps the captured fight if a realtime update arrives during confirmation',async()=>{
    let resolve!: (value:string|null)=>void;
    const {component,store}=setup(new Promise(r=>resolve=r));
    const pending=component.declare('wala');
    store.current.mockReturnValue({match:{id:'fight-136',match_number:136,status:'in_progress'},event_name:'Other event'});
    resolve('');await pending;
    expect(store.api.submit.mock.calls[0][0]).toBe('fight-135');
  });
  it('reuses the request key on an uncertain retry',async()=>{
    const {component,store}=setup();
    store.api.submit.mockReturnValue(throwError(()=>new ApiError(0,'network','Connection lost')));
    await component.declare('wala');await component.declare('wala');
    expect(store.api.submit.mock.calls[0][1].request_id).toBe(store.api.submit.mock.calls[1][1].request_id);
    expect(component.actionError()).toBe('Connection lost');expect(store.reconcile).toHaveBeenCalledTimes(2);
  });
  it('blocks declarations when the current fight cannot receive a result',async()=>{
    const {component,store,confirm}=setup();store.canSubmit=()=>false;
    await component.declare('meron');
    expect(confirm.ask).not.toHaveBeenCalled();expect(store.api.submit).not.toHaveBeenCalled();
  });
});

it('uses a fresh request key when a known declaration has been returned and resubmitted', async () => {
  const {component,store}=setup();
  await component.declare('meron');
  // The store permits another submission only once canonical state is returned/closed.
  await component.declare('meron');
  expect(store.api.submit.mock.calls[0][1].request_id).not.toBe(store.api.submit.mock.calls[1][1].request_id);
});

describe('Declarator action visibility', () => {
  it.each([
    {submitter:true,approver:false,title:'Declare winner',declare:true,review:false},
    {submitter:false,approver:true,title:'no approval is needed',declare:false,review:false},
    {submitter:false,approver:false,title:'Declaration access',declare:false,review:false},
    {submitter:true,approver:true,title:'Declare winner',declare:true,review:false},
  ])('renders the permitted controls for $title ($submitter/$approver)', permission => {
    TestBed.configureTestingModule({providers:[
      {provide:ConfirmService,useValue:{ask:vi.fn()}},
      {provide:MatDialog,useValue:{open:vi.fn()}},
    ]});
    const store = { quickBets: signal(defaultQuickBets()),
      submitter:()=>permission.submitter,approver:()=>permission.approver,
      auth:{user:()=>({username:'declarator'})},realtime:{state:()=> 'open'},liveEnabled:()=>true,
      eventId:()=>null,events:()=>[],monitor:()=>null,current:()=>null,currentBook:()=>null,betting:()=>null,settlements:()=>[],loading:()=>false,
      error:()=>null,approvalNotice:()=>'',canSubmit:()=>false,denied:()=>false,empty:()=>false,submitHint:()=>null,onlinePlayers:()=>2,onlineList:()=>null,ghost:()=>null,
      records:{rows:()=>[],loading:()=>false,error:()=>null,total:()=>0,page:()=>1,pageSize:()=>25,filter:()=>null},
    };
    // Keep the real dashboard template; child widgets are outside this control-visibility test.
    TestBed.overrideComponent(DeclaratorDashboard,{set:{imports:[DatePipe, ArenaPointsPipe],schemas:[NO_ERRORS_SCHEMA],
      providers:[{provide:DeclaratorStore,useValue:store}]}});
    const fixture=TestBed.createComponent(DeclaratorDashboard);fixture.detectChanges();
    const text=fixture.nativeElement.textContent as string;
    expect(text).toContain(permission.title);
    expect(!!fixture.nativeElement.querySelector('button.winner.meron')).toBe(permission.declare);
    expect(!!fixture.nativeElement.querySelector('[data-testid="end-event"]')).toBe(permission.submitter);
    expect(text).toContain('Online players');
    expect(text.includes('Review current declaration')).toBe(permission.review);
    expect(text.includes('check your Declarator account type and role')).toBe(!permission.submitter && !permission.approver);
  });
});

describe('Declarator access and empty states', () => {
  function render(state: {denied: boolean; empty: boolean}) {
    TestBed.configureTestingModule({providers:[{provide:ConfirmService,useValue:{ask:vi.fn()}},{provide:MatDialog,useValue:{open:vi.fn()}}]});
    const store = { quickBets: signal(defaultQuickBets()),
      submitter:()=>false,approver:()=>false,auth:{user:()=>({username:'declarator'})},realtime:{state:()=>'open'},liveEnabled:()=>true,
      eventId:()=>null,events:()=>[],monitor:()=>null,current:()=>null,currentBook:()=>null,betting:()=>null,settlements:()=>[],loading:()=>false,error:()=>null,approvalNotice:()=>'',
      canSubmit:()=>false,denied:()=>state.denied,empty:()=>state.empty,submitHint:()=>null,onlinePlayers:()=>null,onlineList:()=>null,ghost:()=>null,
      records:{rows:()=>[],loading:()=>false,error:()=>null,total:()=>0,page:()=>1,pageSize:()=>25,filter:()=>null},
    };
    TestBed.overrideComponent(DeclaratorDashboard,{set:{imports:[DatePipe],schemas:[NO_ERRORS_SCHEMA],providers:[{provide:DeclaratorStore,useValue:store}]}});
    const fixture=TestBed.createComponent(DeclaratorDashboard);fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }
  it('shows one access message instead of a broken dashboard when the API refuses the account', () => {
    const el = render({denied:true,empty:false});
    expect(el.querySelector('app-empty-state')?.getAttribute('title')).toBe('Declarator access required');
    expect(el.querySelector('.desk')).toBeNull();
  });
  it('explains an account with no assigned events', () => {
    const el = render({denied:false,empty:true});
    expect(el.querySelector('app-empty-state')?.getAttribute('title')).toBe('No assigned events');
    expect(el.querySelector('.desk')).toBeNull();
  });
});

describe('Declaration wording', () => {
  it('asks the exact confirmation question for each outcome', () => {
    expect(confirmQuestion('wala',135)).toBe('Are you sure you want to declare WALA as the winner of Fight #135?');
    expect(confirmQuestion('draw',135)).toContain('Fight #135 a DRAW');
    expect(confirmQuestion('cancelled',135)).toContain('becomes void');
  });
  it('shows a cancellation reason only for cancelled fights', () => {
    const base = {match:{cancel_reason:'Injury'},official_winner:'cancelled',declaration:null};
    expect(cancellationReason(base as never)).toBe('Injury');
    expect(cancellationReason({...base,official_winner:'meron'} as never)).toBeNull();
    expect(cancellationReason({match:{cancel_reason:null},official_winner:null,declaration:{outcome:'cancelled',reason:'Pending reason'}} as never)).toBe('Pending reason');
  });
});


describe('Declarator betting switch', () => {
  const closed: BettingControl = {match_id:'fight-36',match_number:36,match_status:'scheduled',status:'closed',book_state:null,
    can_open:true,can_close:false,blocked_reason:null,next_match_number:null};
  function rig(control: BettingControl | null, submitter = true, monitor: unknown = null, book: unknown = null) {
    TestBed.configureTestingModule({providers:[{provide:ConfirmService,useValue:{ask:vi.fn()}},{provide:MatDialog,useValue:{open:vi.fn()}}]});
    const opened: BettingControl = {...closed,match_status:'in_progress',status:'open',book_state:'open',can_open:false,can_close:true};
    const store = { quickBets: signal(defaultQuickBets()),
      submitter:()=>submitter,approver:()=>false,auth:{user:()=>({username:'declarator'})},realtime:{state:()=>'open'},liveEnabled:()=>true,
      eventId:()=>null,events:()=>[],monitor:()=>monitor,
      current:()=>book ? {match:{id:'fight-6',match_number:6,status:'in_progress'}} : null,
      currentBook:()=>book,betting:vi.fn(()=>control),settlements:()=>[],loading:()=>false,
      error:()=>null,approvalNotice:()=>'',canSubmit:()=>false,denied:()=>false,empty:()=>false,submitHint:()=>null,onlinePlayers:()=>2,onlineList:()=>null,ghost:()=>null,
      records:{rows:()=>[],loading:()=>false,error:()=>null,total:()=>0,page:()=>1,pageSize:()=>25,filter:()=>null},
      api:{openBetting:vi.fn(()=>of(opened)),closeBetting:vi.fn(()=>of({...closed,match_status:'in_progress'})),
        addDummyBet:vi.fn(()=>of({})),
        openEventBetting:vi.fn(()=>of({...opened,match_id:'fight-3',match_number:3}))},
      applyBetting:vi.fn(),reconcile:vi.fn(),
    };
    TestBed.overrideComponent(DeclaratorDashboard,{set:{imports:[DatePipe, ArenaPointsPipe],schemas:[NO_ERRORS_SCHEMA],providers:[{provide:DeclaratorStore,useValue:store}]}});
    const fixture=TestBed.createComponent(DeclaratorDashboard);fixture.detectChanges();
    const el=fixture.nativeElement as HTMLElement;
    const button=(cls:string)=>el.querySelector(`button.${cls}`) as HTMLButtonElement;
    return {fixture,el,store,button,component:fixture.componentInstance,opened};
  }
  it('lists every player stake on Meron, Wala and Draw', () => {
    expect(profitPercent('1.83')).toBe('83%');
    expect(profitPercent('8.00')).toBe('700%');
    const book = {
      state: 'open', final: false, bet_count: 2, draw_multiplier: '8.00', outcome: null,
      pools: { meron: '1000.00', wala: '250.00', draw: '40.00' },
      odds: { meron_display: '1.13', wala_display: null },
      obligations: null, settlement: null,
      players: [
        { username: 'player1', full_name: 'Player 1', meron: '1000.00', wala: '0.00', draw: '0.00' },
        { username: 'player2', full_name: null, meron: '0.00', wala: '250.00', draw: '40.00' },
      ],
    };
    const { el } = rig(closed, true, null, { ...book, dummy_meron: '500.00', dummy_wala: '0.00', pools: { ...book.pools, meron: '1500.00' } });
    // Live bets table: No. | Username | Bet | Amount | Possible win. Ghost rows are never paid.
    const rows = [...el.querySelectorAll('[data-testid="activity-table"] tbody tr')].map(r => [...r.querySelectorAll('td')].map(td => td.textContent!.replace(/\s+/g, ' ').trim()));
    expect(rows).toEqual([
      ['*', 'Ghost betGhost', 'MERON', '500.00', '—'],
      ['1', 'player1', 'MERON', '1,000.00', '13%1,130.00'],
      ['2', 'player2', 'WALA', '250.00', '—'],
      ['3', 'player2', 'DRAW', '40.00', '700%320.00'],
    ]);
    expect(el.querySelector('[data-testid="activity-table"] tbody tr')!.getAttribute('data-ghost')).toBe('true');
  });
  it('adds a quick dummy amount on the open fight', async () => {
    const book = {
      match_id: 'fight-8', state: 'open', accepting: true, final: false, bet_count: 1, draw_multiplier: '8.00', outcome: null,
      pools: { meron: '300.00', wala: '0.00', draw: '0.00' },
      odds: { meron_display: '2.00', wala_display: null },
      obligations: null, settlement: null, players: [], dummy_meron: '0.00', dummy_wala: '0.00',
    };
    const { component, store, el, fixture } = rig(closed, true, null, book);
    expect(el.textContent).toContain('Manual ghost betting');
    expect(el.textContent).toContain('never taken from or paid to a balance');
    expect(el.querySelector('.ghost-grid .dummy-row input')).toBeNull();
    const labels = [...el.querySelectorAll('.dummy-amount')].map(b => b.textContent!.trim());
    expect(labels).toEqual(['+5K', '+10K', '+20K', '+50K', '+5K', '+10K', '+20K', '+50K']);
    expect(el.querySelector('.dummy-sides')).toBeNull();
    (el.querySelector('.dummy-amount[data-side="wala"][data-amount="50000"]') as HTMLButtonElement).click();
    await fixture.whenStable();
    expect(store.api.addDummyBet).toHaveBeenCalledWith('fight-8', expect.objectContaining({ side: 'wala', amount: '50000.00' }));
    expect(store.reconcile).toHaveBeenCalled();
    expect(component.message()).toContain('WALA +50K');
  });
  it('shows betting info with totals, payout, actual bets and ghost bets separately', () => {
    const book = {
      match_id: 'fight-13', state: 'open', accepting: true, final: false, bet_count: 1, draw_multiplier: '8.00', outcome: null,
      pools: { meron: '1550000.00', wala: '905000.00', draw: '0.00' },
      odds: { meron_display: '1.43', wala_display: '2.44' },
      obligations: null, settlement: null, players: [], dummy_meron: '1550000.00', dummy_wala: '900000.00',
    };
    const { el } = rig(closed, true, null, book);
    const field = (f: string) => el.querySelector(`[data-field="${f}"]`)!.textContent!.trim();
    expect(field('players-meron')).toBe('0.00');
    expect(field('players-wala')).toBe('5,000.00');
    expect(field('dummy-meron')).toContain('1,550,000.00');
    expect(field('dummy-wala')).toContain('900,000.00');
    expect(field('total-meron')).toBe('1,550,000.00');
    expect(field('payout-wala')).toBe('2.44');
    expect(el.querySelector('[data-testid="odds-form"]')).toBeNull();
    expect(el.textContent).not.toContain('Meron wins returns');
    const rows = [...el.querySelectorAll('[data-testid="settlement-summary"] div')]
      .map(d => [d.querySelector('dt')!.textContent!.trim(), d.querySelector('dd')!.textContent!.trim()]);
    expect(rows).toEqual([['Result', 'Awaiting result'], ['Credited to winners', '—'], ['Refunded', '—'], ['Deduction', '—']]);
  });
  it('shows the settlement summary after a result', () => {
    const book = {
      match_id: 'fight-9', state: 'settled', accepting: false, final: true, bet_count: 1, draw_multiplier: '8.00', outcome: 'meron',
      pools: { meron: '1000.00', wala: '0.00', draw: '0.00' },
      odds: { meron_display: '1.83', wala_display: '1.83' },
      obligations: null,
      settlement: { outcome: 'meron', revision: 1, stakes: '1000.00', paid_to_winners: '1830.00', refunds: '0.00', commission: '0.00', uncollected: '0.00', settled_at: null },
      players: [], dummy_meron: '0.00', dummy_wala: '0.00',
    };
    const { el } = rig(closed, true, null, book);
    const text = el.textContent!.replace(/\s+/g, ' ');
    expect(text).toContain('Result');
    expect(text).toContain('Meron wins');
    expect(text).toContain('Credited to winners');
    expect(text).toContain('1,830.00');
    expect(text).toContain('Refunded');
    expect(text).toContain('Deduction');
    expect(text).not.toContain('Meron wins returns');
  });
  it('refuses ghost points when betting is closed', async () => {
    const book = {
      match_id: 'fight-12', state: 'closed', accepting: false, final: true, bet_count: 2, draw_multiplier: '8.00', outcome: null,
      pools: { meron: '1000.00', wala: '400.00', draw: '0.00' },
      odds: { meron_display: '1.26', wala_display: '3.15' },
      obligations: null, settlement: null, players: [], dummy_meron: '0.00', dummy_wala: '0.00',
    };
    const { component, store, el, fixture } = rig(closed, true, null, book);
    const button = el.querySelector('.dummy-amount[data-side="meron"][data-amount="5000"]') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.classList).toContain('meron');
    button.click();
    await fixture.whenStable();
    expect(store.api.addDummyBet).not.toHaveBeenCalled();
  });
  it('adds every rapid click immediately with unique request IDs', async () => {
    const book = {match_id:'fight-8', state:'open', pools:{meron:'0.00',wala:'0.00',draw:'0.00'}, odds:{}, players:[], dummy_meron:'0.00',dummy_wala:'0.00'};
    const {component,store} = rig(closed, true, null, book);
    const requests = [component.addDummy('meron',5000), component.addDummy('meron',5000), component.addDummy('meron',50000)];
    expect(component.totalPool(book as any,'meron')).toBe('60000.00');
    await Promise.all(requests);
    expect(store.api.addDummyBet).toHaveBeenCalledTimes(3);
    const calls = store.api.addDummyBet.mock.calls as unknown as [string,{request_id:string}][];
    expect(new Set(calls.map(c=>c[1].request_id)).size).toBe(3);
    book.dummy_meron='60000.00';book.pools.meron='60000.00';
    expect(component.totalPool(book as any,'meron')).toBe('60000.00');
  });
  it('rolls back failed ghost clicks immediately', async () => {
    const book = {match_id:'fight-8',state:'open',pools:{meron:'100.00',wala:'0.00',draw:'0.00'},odds:{},players:[],dummy_meron:'0.00',dummy_wala:'0.00'};
    const {component,store} = rig(closed,true,null,book);
    store.api.addDummyBet.mockReturnValue(throwError(()=>new Error('Rejected')) as never);
    const request = component.addDummy('meron',5000);
    expect(component.totalPool(book as any,'meron')).toBe('5100.00');
    await request;
    expect(component.totalPool(book as any,'meron')).toBe('100.00');
    expect(component.dummyError()).toBeTruthy();
  });
  it('renders independent configured buttons and immediately adds 7K to Meron', async () => {
    const book = {match_id:'fight-8',state:'open',pools:{meron:'0.00',wala:'0.00',draw:'0.00'},odds:{},players:[],dummy_meron:'0.00',dummy_wala:'0.00'};
    const {component,store,fixture,el} = rig(closed,true,null,book);
    store.quickBets.set({...defaultQuickBets(),meron:['7000.00','8000.00','15000.00','30000.00']});
    fixture.detectChanges();
    const meron = el.querySelector('.dummy-amount[data-side="meron"][data-amount="7000"]') as HTMLButtonElement;
    expect(meron.textContent?.trim()).toBe('+7K');
    expect(el.querySelector('.dummy-amount[data-side="wala"][data-amount="5000"]')).toBeTruthy();
    meron.click();
    expect(component.totalPool(book as any,'meron')).toBe('7000.00');
    await fixture.whenStable();
    expect(store.api.addDummyBet).toHaveBeenCalledTimes(1);
    expect(store.api.addDummyBet).toHaveBeenCalledWith('fight-8',expect.objectContaining({side:'meron',amount:'7000.00'}));
  });
  it('shows the server status and fight number', () => {
    const {el,button}=rig(closed);
    expect(el.querySelector('[data-testid="betting-status"]')!.textContent!.trim()).toBe('CLOSED');
    expect(el.querySelector('[data-testid="betting-fight"]')!.textContent).toContain('Fight #36');
    expect(el.querySelector('[data-testid="next-fight"]')).toBeNull();
    expect(el.textContent).not.toContain('Simulated play');
    expect(button('open-betting').disabled).toBe(false);
    expect(button('close-betting')).toBeNull();
  });
  it('opens betting through the API and applies the returned state', async () => {
    const {component,store,opened}=rig(closed);
    await component.setBetting(true);
    expect(store.api.openBetting).toHaveBeenCalledWith('fight-36');
    expect(store.applyBetting).toHaveBeenCalledWith(opened);
    expect(component.message()).toBe('Fight #36: betting OPEN.');
  });
  it('closes betting and keeps the fight number', async () => {
    const {component,store}=rig({...closed,match_status:'in_progress',status:'open',can_open:false,can_close:true});
    await component.setBetting(false);
    expect(store.api.closeBetting).toHaveBeenCalledWith('fight-36');
    expect(component.message()).toBe('Fight #36: betting CLOSED.');
  });
  it('sends nothing when the server does not allow the action', async () => {
    const {component,store,button}=rig({...closed,can_open:false,blocked_reason:'Fight #36 has a result awaiting approval.'});
    await component.setBetting(true);await component.setBetting(false);
    expect(store.api.openBetting).not.toHaveBeenCalled();expect(store.api.closeBetting).not.toHaveBeenCalled();
    expect(button('open-betting').disabled).toBe(true);
    expect(button('close-betting')).toBeNull();
  });
  it('reports a refusal and reloads the canonical state', async () => {
    const {component,store}=rig(closed);
    store.api.openBetting.mockReturnValue(throwError(()=>new ApiError(409,'result_finalized','Fight #36 already has an official result')) as never);
    await component.setBetting(true);
    expect(component.actionError()).toContain('official result');expect(store.reconcile).toHaveBeenCalled();expect(component.busy()).toBe(false);
  });
  it('hides the switch buttons from accounts that are not the Declarator', () => {
    const {el}=rig(closed,false);
    expect(el.querySelector('button.open-betting')).toBeNull();
    expect(el.textContent).toContain('Only the assigned Declarator opens and closes betting.');
  });
  it('has no fight entry form', () => {
    const monitor={event:{id:'ev',status:'live'},matches:[{id:'fight-36',match_number:36,meron_name:'Meron',wala_name:'Wala',status:'scheduled'}]};
    const {el}=rig(closed,true,monitor);
    expect(el.querySelector('[data-testid="fight-entries"]')).toBeNull();
    expect(el.querySelector('input[name="meron"]')).toBeNull();
    expect(el.textContent).not.toContain('Prepare fight');
  });
  it('opens betting on an empty card through the event, which adds the next fight', async () => {
    const none:BettingControl={...closed,match_id:null,match_number:null,match_status:null,can_open:true};
    const monitor={event:{id:'ev',status:'live'},matches:[{id:'f1',match_number:1,status:'completed'},{id:'f2',match_number:2,status:'cancelled'}]};
    const {component,store,button}=rig(none,true,monitor);
    expect(button('open-betting').disabled).toBe(false);
    await component.setBetting(true);
    expect(store.api.openEventBetting).toHaveBeenCalledWith('ev');
    expect(store.api.openBetting).not.toHaveBeenCalled();
    expect(store.reconcile).toHaveBeenCalled();
    expect(component.message()).toBe('Fight #3: betting OPEN.');
  });
});

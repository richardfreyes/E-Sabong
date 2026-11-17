import { QuickBetSettingsApi, defaultQuickBets } from '@shared/services';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject, filter, map, of, throwError } from 'rxjs';
import { ApiError, AuthStore, BrandingService, DeclaratorApi, DeclaratorMonitor, RealtimeService } from '@shared/services';
import { DeclaratorStore } from './store';

function setup() {
  const requests: Subject<DeclaratorMonitor>[]=[];
  const actor=signal({account_type:'declarator',permissions:['results.submit']});
  const messages=new Subject<{type:string;data:Record<string,unknown>}>();
  const api={events:vi.fn(()=>of([{id:'a',status:'live'},{id:'b',status:'live'}])),
    monitor:vi.fn(()=>{const request=new Subject<DeclaratorMonitor>();requests.push(request);return request;}),
    liveBook:vi.fn(()=>of(null)),
    records:vi.fn(()=>of({items:[],total:0,page:1,page_size:25})),
    ghostAuto:vi.fn(()=>of({active:false,runs:0,added_meron:'0.00',added_wala:'0.00'})),onlinePlayers:vi.fn(()=>of({players:2}))};
  TestBed.configureTestingModule({providers:[DeclaratorStore,{provide:QuickBetSettingsApi,useValue:{get:vi.fn(()=>of(defaultQuickBets()))}},{provide:DeclaratorApi,useValue:api},
    {provide:BrandingService,useValue:{config:signal({feature_flags:{}})}},
    {provide:AuthStore,useValue:{user:actor,hasPermission:(p:string)=>actor().permissions.includes(p)}},
    {provide:RealtimeService,useValue:{state:signal('open'),on:(...types:string[])=>messages.pipe(filter(m=>types.includes(m.type)),map(m=>m.data))}}]});
  return {store:TestBed.inject(DeclaratorStore),api,requests,messages,actor};
}
const snapshot=(id:string, revision=1)=>({event:{id,status:'live'},matches:[{id:'m',result:{revision}}],current:null}) as DeclaratorMonitor;

describe('Declarator canonical realtime state',()=>{
  beforeEach(()=>vi.useFakeTimers());afterEach(()=>{TestBed.resetTestingModule();vi.useRealTimers();});
  it('reloads tenant quick buttons on settings notifications and reconnection',()=>{
    const {store,messages}=setup();
    const api = TestBed.inject(QuickBetSettingsApi);
    vi.mocked(api.get).mockReturnValue(of({...defaultQuickBets(),meron:['7000.00','8000.00','15000.00','30000.00']}));
    messages.next({type:'declarator.settings_updated',data:{}});
    expect(store.quickBets().meron[0]).toBe('7000.00');
    expect(store.quickBets().wala).toEqual(defaultQuickBets().wala);
    messages.next({type:'connected',data:{}});
    expect(api.get).toHaveBeenCalledTimes(3);
  });
  it('discards a late response for a previously selected event',()=>{
    const {store,requests}=setup();store.selectEvent('b');
    requests[1].next(snapshot('b'));requests[0].next(snapshot('a'));
    expect(store.monitor()?.event.id).toBe('b');
  });
  it('coalesces repeated/out-of-order notifications and refetches official history',()=>{
    const {store,api,requests,messages}=setup();requests[0].next(snapshot('a',2));requests[0].complete();
    for(const revision of [2,1,2]) messages.next({type:'match.result_corrected',data:{revision}});
    vi.advanceTimersByTime(151);
    expect(api.monitor).toHaveBeenCalledTimes(2);
    expect(store.monitor()?.matches[0].result?.revision).toBe(2);
    requests[1].next(snapshot('a',2));expect(store.monitor()?.matches[0].result?.revision).toBe(2);
  });
  it('reloads assignments and canonical state on WebSocket reconnection',()=>{
    const {api,messages,requests}=setup();requests[0].complete();messages.next({type:'connected',data:{}});
    expect(api.events).toHaveBeenCalledTimes(2);expect(api.monitor).toHaveBeenCalledTimes(2);
  });
  it('lists another event going live without reloading the selected desk',()=>{
    const {store,api,requests,messages}=setup();requests[0].complete();
    api.events.mockReturnValue(of([{id:'a',status:'live'},{id:'b',status:'live'},{id:'c',status:'live'}]));
    messages.next({type:'event.updated',data:{id:'c',status:'live'}});
    expect(store.events().map(e=>e.id)).toEqual(['a','b','c']);
    expect(store.eventId()).toBe('a');
    expect(api.monitor).toHaveBeenCalledTimes(1);
    messages.next({type:'event.updated',data:{id:'a',status:'live'}});
    expect(api.monitor).toHaveBeenCalledTimes(2);
  });
  it('explains a missing workspace API and recovers after the backend is updated',()=>{
    const {store,api,requests}=setup();
    api.events.mockReturnValueOnce(throwError(()=>new ApiError(404,'http_error','Not Found')));
    store.refresh();
    expect(store.error()).toContain('update and restart the API');
    expect(store.loading()).toBe(false);
    expect(store.canSubmit()).toBe(false);
    store.refresh();requests.at(-1)!.next(snapshot('a'));
    expect(store.error()).toBeNull();
    expect(store.monitor()?.event.id).toBe('a');
  });
  it('preserves permission errors rather than misreporting a missing API',()=>{
    const {store,api}=setup();
    api.events.mockReturnValueOnce(throwError(()=>new ApiError(403,'forbidden','Declarator access required')));
    store.refresh();
    // A refusal is a configuration problem: it gets its own state instead of a retryable error.
    expect(store.denied()).toBe(true);
    expect(store.error()).toBeNull();
    expect(store.canSubmit()).toBe(false);
  });
});

it('shows approval notifications only to an authorized reviewer', () => {
  const {store,messages,actor}=setup();
  const notification={type:'notification.created',data:{type:'result.approval_required',title:'Fight #135 awaits result approval'}};
  messages.next(notification);expect(store.approvalNotice()).toBe('');
  actor.update(user=>({...user,permissions:[...user.permissions,'results.approve']}));
  messages.next(notification);expect(store.approvalNotice()).toContain('#135');
  messages.next({type:'notification.created',data:{type:'other',title:'Unrelated'}});
  expect(store.approvalNotice()).toContain('#135');
  TestBed.resetTestingModule();
});

it('reads the betting switch from the server snapshot and refreshes it on book frames', () => {
  vi.useFakeTimers();
  const {store,api,requests,messages}=setup();
  const betting={match_id:'m36',match_number:36,match_status:'scheduled',status:'closed',book_state:null,can_open:true,can_close:false,blocked_reason:null,next_match_number:null} as const;
  requests[0].next({...snapshot('a'),current:{match:{id:'m36',match_number:35,status:'completed'},workflow_status:'finalized'},betting} as unknown as DeclaratorMonitor);
  expect(store.betting()?.match_number).toBe(36);
  expect(store.submitHint()).toBe('Open betting to start Fight #36.');
  store.applyBetting({...betting,match_status:'in_progress',status:'open',book_state:'open',can_open:false,can_close:true});
  expect(store.betting()?.status).toBe('open');
  messages.next({type:'book.updated',data:{match_id:'m36'}});
  vi.advanceTimersByTime(751);
  expect(api.monitor).toHaveBeenCalledTimes(1);
  expect(api.liveBook).toHaveBeenCalledWith('m36');
  vi.useRealTimers();
  TestBed.resetTestingModule();
});

it('enables the result buttons only after betting is closed', () => {
  const {store,requests}=setup();
  const open={match_id:'m36',match_number:36,match_status:'in_progress',status:'open',book_state:'open',can_open:false,can_close:true,blocked_reason:null,next_match_number:null} as const;
  const current={match:{id:'m36',match_number:36,status:'in_progress'},workflow_status:'pending'};
  requests[0].next({...snapshot('a'),current,betting:open} as unknown as DeclaratorMonitor);
  expect(store.canSubmit()).toBe(false);
  expect(store.submitHint()).toBe('Close betting to declare the result of Fight #36.');
  store.applyBetting({...open,status:'closed',book_state:'closed',can_open:false,can_close:false});
  expect(store.canSubmit()).toBe(true);
  expect(store.submitHint()).toBe('Declare Meron, Wala, or Draw for Fight #36. Betting stays closed until then.');
  TestBed.resetTestingModule();
});

it('asks to open betting when the live card has no fight yet', () => {
  const {store,requests}=setup();
  const none={match_id:null,match_number:null,match_status:null,status:'closed',book_state:null,can_open:true,can_close:false,blocked_reason:null,next_match_number:null} as const;
  requests[0].next({...snapshot('a'),betting:none} as unknown as DeclaratorMonitor);
  expect(store.canSubmit()).toBe(false);
  expect(store.submitHint()).toBe('Open betting to start the next fight.');
  TestBed.resetTestingModule();
});

it('book traffic refreshes only the selected fight and leaves history/desk untouched', () => {
  vi.useFakeTimers();
  const {store, api, requests, messages} = setup();
  requests[0].next({...snapshot('a'),current:{match:{id:'m'},workflow_status:'pending'},books:[]} as unknown as DeclaratorMonitor);
  requests[0].complete(); vi.advanceTimersByTime(101);
  const records = api.records.mock.calls.length, online = api.onlinePlayers.mock.calls.length;
  for (let i=0; i<100; i++) messages.next({type:'book.updated',data:{event_id:'a',match_id:'m'}});
  vi.advanceTimersByTime(751);
  expect(api.liveBook).toHaveBeenCalledTimes(1);
  expect(api.monitor).toHaveBeenCalledTimes(1);
  expect(api.records).toHaveBeenCalledTimes(records);
  expect(api.onlinePlayers).toHaveBeenCalledTimes(online);
  messages.next({type:'book.updated',data:{event_id:'b',match_id:'other'}});
  vi.advanceTimersByTime(751); expect(api.liveBook).toHaveBeenCalledTimes(1);
  TestBed.resetTestingModule(); vi.useRealTimers();
});

it('reloads the betting switch when someone else opens betting (book state change only)', () => {
  vi.useFakeTimers();
  const {store, api, requests, messages} = setup();
  const monitor = (state: string | null, status: string) => ({...snapshot('a'), current: {match: {id: 'm'}, workflow_status: 'pending'},
    books: state ? [{match_id: 'm', state, version: 1}] : [], betting: {status, match_id: 'm'}}) as unknown as DeclaratorMonitor;
  requests[0].next(monitor(null, 'closed')); requests[0].complete(); vi.advanceTimersByTime(101);
  // The owner (shared arena) or an administrator opens betting: only book.updated arrives.
  messages.next({type: 'book.updated', data: {event_id: 'a', match_id: 'm', state: 'open', version: 1}});
  vi.advanceTimersByTime(151);
  expect(api.monitor).toHaveBeenCalledTimes(2);
  requests[1].next(monitor('open', 'open')); requests[1].complete();
  expect(store.betting()?.status).toBe('open');
  // Stake-only traffic in the same state stays on the cheap single-book path.
  const reads = api.monitor.mock.calls.length;
  for (let i = 0; i < 50; i++) messages.next({type: 'book.updated', data: {event_id: 'a', match_id: 'm', state: 'open', version: 2 + i}});
  vi.advanceTimersByTime(751);
  expect(api.monitor).toHaveBeenCalledTimes(reads);
  expect(api.liveBook).toHaveBeenCalled();
  // A book for a fight the desk does not show as current (the next fight) reloads the card too.
  messages.next({type: 'book.updated', data: {event_id: 'a', match_id: 'next', state: 'open', version: 1}});
  vi.advanceTimersByTime(151);
  expect(api.monitor).toHaveBeenCalledTimes(reads + 1);
  TestBed.resetTestingModule(); vi.useRealTimers();
});

it('keeps timers/subscriptions and request rates bounded across three simulated hours', () => {
  vi.useFakeTimers();
  const {api, requests, messages} = setup();
  const loaded = {...snapshot('a'),current:{match:{id:'m'},workflow_status:'pending'},books:[]} as unknown as DeclaratorMonitor;
  requests[0].next(loaded); requests[0].complete();
  api.monitor.mockImplementation(() => of(loaded) as never);
  const timers = vi.getTimerCount();
  for (let second=0;second<10800;second++) {
    messages.next({type:'book.updated',data:{event_id:'a',match_id:'m'}});
    vi.advanceTimersByTime(1000);
    if (second % 600 === 599) expect(vi.getTimerCount()).toBe(timers);
  }
  expect(api.monitor.mock.calls.length).toBeLessThanOrEqual(362);
  expect(api.records.mock.calls.length).toBeLessThanOrEqual(362);
  expect(api.liveBook.mock.calls.length).toBe(10800);
  TestBed.resetTestingModule();
  expect(messages.observed).toBe(false);
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers();
});

describe('Declarator book frames and polling', () => {
  beforeEach(() => vi.useFakeTimers()); afterEach(() => { TestBed.resetTestingModule(); vi.useRealTimers(); });
  const book = { match_id: 'm', state: 'open', version: 5, bet_count: 2, accepting: true, final: false,
    pools: { meron: '1500.00', wala: '400.00', draw: '0.00' }, dummy_meron: '1000.00', dummy_wala: '0.00',
    odds: { meron_display: '1.40', wala_display: '2.26' }, players: [{ username: 'p1', meron: '500.00', wala: '400.00', draw: '0.00' }] };
  const desk = () => ({ event: { id: 'a', status: 'live' }, matches: [], current: { match: { id: 'm', match_number: 7, status: 'in_progress' } },
    books: [structuredClone(book)] }) as unknown as DeclaratorMonitor;
  const frame = (over: Record<string, unknown>) => ({ type: 'book.updated', data: { event_id: 'a', match_id: 'm', state: 'open', bet_count: 2,
    accepting: true, final: false, pools: { meron: '6500.00', wala: '400.00', draw: '0.00' }, odds: { meron_display: '1.12', wala_display: '3.20' }, ...over } });

  it('applies ghost and odds frames in place, exactly, without fetching the book', () => {
    const { store, api, requests, messages } = setup(); requests[0].next(desk()); requests[0].complete();
    messages.next(frame({ version: 6 }));
    vi.advanceTimersByTime(500);
    expect(api.liveBook).not.toHaveBeenCalled();
    const b = store.currentBook()!;
    expect([b.version, b.pools.meron, b.dummy_meron, b.dummy_wala, b.odds.meron_display]).toEqual([6, '6500.00', '6000.00', '0.00', '1.12']);
    expect(b.players).toEqual(book.players); // player rows are untouched
    // The Declarator's own answer carries the ghost totals and wins over the derivation.
    expect(store.applyBookFrame({ match_id: 'm', version: 7, state: 'open', bet_count: 2, pools: { meron: '6500.10', wala: '400.00', draw: '0.00' },
      odds: b.odds, dummy_meron: '6000.10', dummy_wala: '0.00' })).toBe(true);
    expect(store.currentBook()!.dummy_meron).toBe('6000.10');
    // An echo or an older frame changes nothing and needs no request.
    messages.next(frame({ version: 6, pools: { meron: '1.00', wala: '1.00', draw: '0.00' } }));
    vi.advanceTimersByTime(500);
    expect(store.currentBook()!.pools.meron).toBe('6500.10');
    expect(api.liveBook).not.toHaveBeenCalled();
  });

  it('never lets a late frame or slow read put older totals back after a player-bet frame', () => {
    const { store, api, requests, messages } = setup(); requests[0].next(desk()); requests[0].complete();
    api.liveBook.mockReturnValue(new Subject() as never); // the player-row reload is still in flight
    messages.next(frame({ version: 10, bet_count: 3, pools: { meron: '9000.00', wala: '400.00', draw: '0.00' }, odds: { meron_display: '1.00', wala_display: '2.66' } }));
    expect(store.currentBook()!.pools.meron).toBe('9000.00');
    // An older frame (v8) arriving now is ignored even though the book itself still says v5.
    messages.next(frame({ version: 8, bet_count: 2 }));
    expect([store.currentBook()!.pools.meron, store.currentBook()!.odds.meron_display]).toEqual(['9000.00', '1.00']);
    // So is a monitor snapshot that was read before v10.
    store.reconcile(); requests[1].next({ ...desk(), books: [{ ...structuredClone(book), version: 9 }] } as unknown as DeclaratorMonitor);
    expect(store.currentBook()!.pools.meron).toBe('9000.00');
    // The Declarator's own answer in a player-bet frame carries the ghost split, so it is applied.
    store.applyBookFrame({ match_id: 'm', version: 11, state: 'open', bet_count: 3, pools: { meron: '14000.00', wala: '400.00', draw: '0.00' },
      odds: { meron: '1', wala: '2.66', meron_display: '1.00', wala_display: '2.66' }, dummy_meron: '6000.00', dummy_wala: '0.00' });
    expect([store.currentBook()!.pools.meron, store.currentBook()!.dummy_meron]).toEqual(['14000.00', '6000.00']);
  });

  it('derives the ghost split of a ghost frame that follows a player-bet frame (no double count)', () => {
    const { store, api, requests, messages } = setup(); requests[0].next(desk()); requests[0].complete();
    api.liveBook.mockReturnValue(new Subject() as never); // player rows still loading
    // A player bets 100 on Wala (bet count 2 -> 3), then the Declarator's +5000 Meron echo arrives.
    messages.next(frame({ version: 6, bet_count: 3, pools: { meron: '1500.00', wala: '500.00', draw: '0.00' } }));
    messages.next(frame({ version: 7, bet_count: 3, pools: { meron: '6500.00', wala: '500.00', draw: '0.00' } }));
    const b = store.currentBook()!;
    expect([b.pools.meron, b.dummy_meron, b.dummy_wala]).toEqual(['6500.00', '6000.00', '0.00']);
    // The Declarator's own answer for the same commit is a repeat now, and changes nothing.
    expect(store.applyBookFrame({ match_id: 'm', version: 7, state: 'open', bet_count: 3, pools: b.pools, odds: b.odds,
      dummy_meron: '6000.00', dummy_wala: '0.00' })).toBe(true);
    expect(store.currentBook()!.dummy_meron).toBe('6000.00');
  });

  it('takes the player rows from a reload that finished after newer ghost frames, and keeps their totals', () => {
    const { store, api, requests, messages } = setup(); requests[0].next(desk()); requests[0].complete();
    const reload = new Subject<any>(); api.liveBook.mockReturnValue(reload as never);
    messages.next(frame({ version: 6, bet_count: 3, pools: { meron: '1500.00', wala: '500.00', draw: '0.00' } }));
    vi.advanceTimersByTime(751); // the reload is sent and reads v6
    messages.next(frame({ version: 7, bet_count: 3, pools: { meron: '6500.00', wala: '500.00', draw: '0.00' } }));
    const rows = [{ username: 'p1', meron: '500.00', wala: '500.00', draw: '0.00' }];
    reload.next({ ...structuredClone(book), version: 6, bet_count: 3, pools: { meron: '1500.00', wala: '500.00', draw: '0.00' }, players: rows }); reload.complete();
    const b = store.currentBook()!;
    expect([b.pools.meron, b.dummy_meron, b.bet_count, b.players]).toEqual(['6500.00', '6000.00', 3, rows]);
  });

  it('ignores a book frame from another workspace', () => {
    const { store, requests, messages, actor } = setup(); requests[0].next(desk()); requests[0].complete();
    actor.set({ ...actor(), tenant: { id: 't1' } } as never);
    messages.next(frame({ version: 6, tenant_id: 't2' }));
    expect(store.currentBook()!.version).toBe(5);
    messages.next(frame({ version: 6, tenant_id: 't1' }));
    expect(store.currentBook()!.version).toBe(6);
  });

  it('fetches the book once for new player bets, and reloads the snapshot when the state changes', () => {
    const { store, api, requests, messages } = setup(); requests[0].next(desk()); requests[0].complete();
    for (let v = 6; v < 30; v++) messages.next(frame({ version: v, bet_count: 3 }));
    // Totals and odds show at once; the ghost split cannot be derived from a player-bet frame, so it stays.
    expect([store.currentBook()!.pools.meron, store.currentBook()!.odds.meron_display, store.currentBook()!.dummy_meron]).toEqual(['6500.00', '1.12', '1000.00']);
    expect(api.liveBook).not.toHaveBeenCalled();
    vi.advanceTimersByTime(751);
    expect(api.liveBook).toHaveBeenCalledTimes(1);
    messages.next(frame({ version: 40, state: 'closed' }));
    vi.advanceTimersByTime(151);
    expect(api.monitor).toHaveBeenCalledTimes(2);
  });

  it('after an action the snapshot read goes out alone; history and desk extras follow it once', () => {
    const { store, api, requests, messages } = setup(); requests[0].next(desk()); requests[0].complete();
    const counts = () => [api.monitor.mock.calls.length, api.records.mock.calls.length,
      api.ghostAuto.mock.calls.length, api.onlinePlayers.mock.calls.length];
    const start = counts();
    // A declaration: the answer and its realtime messages (result, settlement, book state) arrive together.
    store.reconcile();
    for (const type of ['match.result_declared', 'fight.settled', 'declaration.updated'])
      messages.next({ type, data: { event_id: 'a' } });
    messages.next(frame({ version: 9, state: 'settled' }));
    vi.advanceTimersByTime(151);
    expect(counts()).toEqual([start[0] + 1, start[1], start[2], start[3]]); // only the snapshot is in flight
    requests.at(-1)!.next(desk()); requests.at(-1)!.complete();
    // The coalesced realtime change reads the snapshot once more (it may postdate the first read) ...
    expect(api.monitor.mock.calls.length).toBe(start[0] + 2);
    // ... and history, ghost job and presence are read once each, after the snapshot answered. Every
    // change had arrived before that, so these reads already include all of them.
    expect(counts().slice(1)).toEqual([start[1] + 1, start[2] + 1, start[3] + 1]);
    requests.at(-1)!.next(desk()); requests.at(-1)!.complete();
    vi.advanceTimersByTime(1000);
    expect(counts()).toEqual([start[0] + 2, start[1] + 1, start[2] + 1, start[3] + 1]); // nothing else follows
    // A failed snapshot read still lets them through.
    api.monitor.mockReturnValueOnce(throwError(() => new ApiError(503, 'http_error', 'Unavailable')) as never);
    store.reconcile();
    expect(counts().slice(1)).toEqual([start[1] + 2, start[2] + 2, start[3] + 2]);
  });

  it('a realtime change reloads history or desk extras only when the snapshot shows they changed', () => {
    const { api, requests, messages } = setup(); requests[0].next(desk()); requests[0].complete();
    const counts = () => [api.monitor.mock.calls.length, api.records.mock.calls.length,
      api.ghostAuto.mock.calls.length, api.onlinePlayers.mock.calls.length];
    const start = counts();
    // Someone else's change that leaves the card, results and betting switch as they were.
    messages.next({ type: 'match.updated', data: { event_id: 'a' } });
    vi.advanceTimersByTime(151);
    requests.at(-1)!.next(desk()); requests.at(-1)!.complete();
    expect(counts()).toEqual([start[0] + 1, start[1], start[2], start[3]]);
    // A result (an Admin correction): history reloads; the current fight is unchanged, so the extras do not.
    messages.next({ type: 'match.result_corrected', data: { event_id: 'a' } });
    vi.advanceTimersByTime(151);
    requests.at(-1)!.next({ ...desk(), matches: [{ id: 'm', result: { revision: 2 } }] } as unknown as DeclaratorMonitor);
    requests.at(-1)!.complete();
    expect(counts()).toEqual([start[0] + 2, start[1] + 1, start[2], start[3]]);
    // The next fight became current: history and the extras (its stakes, its ghost job) reload.
    messages.next({ type: 'match.updated', data: { event_id: 'a' } });
    vi.advanceTimersByTime(151);
    requests.at(-1)!.next({ ...desk(), matches: [{ id: 'm', result: { revision: 2 } }],
      current: { match: { id: 'n', match_number: 8, status: 'in_progress' } } } as unknown as DeclaratorMonitor);
    requests.at(-1)!.complete();
    expect(counts()).toEqual([start[0] + 3, start[1] + 2, start[2] + 1, start[3] + 1]);
  });

  it('polls nothing but presence while the socket is open; polls everything while it is down', () => {
    const { store, api, requests } = setup(); requests[0].next(desk()); requests[0].complete();
    const realtime = TestBed.inject(RealtimeService);
    const counts = () => [api.events.mock.calls.length, api.monitor.mock.calls.length, api.records.mock.calls.length,
      api.ghostAuto.mock.calls.length, api.onlinePlayers.mock.calls.length];
    const start = counts();
    vi.advanceTimersByTime(60_000);
    expect(counts()).toEqual([start[0], start[1], start[2], start[3], start[4] + 4]); // online every 15 s only
    store.ghost.set({ active: true } as never);
    vi.advanceTimersByTime(15_000);
    expect(api.ghostAuto.mock.calls.length).toBe(start[3] + 1); // a running job's counters are polled
    store.ghost.set({ active: false } as never);
    vi.advanceTimersByTime(45_000);
    expect(api.monitor.mock.calls.length).toBe(start[1] + 1); // the 2-minute canonical check
    realtime.state.set('closed');
    vi.advanceTimersByTime(15_000);
    expect(api.events.mock.calls.length).toBe(start[0] + 1); // fallback polling while disconnected
  });
});

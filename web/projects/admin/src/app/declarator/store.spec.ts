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
  vi.advanceTimersByTime(151);
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
  vi.advanceTimersByTime(151);
  expect(api.liveBook).toHaveBeenCalledTimes(1);
  expect(api.monitor).toHaveBeenCalledTimes(1);
  expect(api.records).toHaveBeenCalledTimes(records);
  expect(api.onlinePlayers).toHaveBeenCalledTimes(online);
  messages.next({type:'book.updated',data:{event_id:'b',match_id:'other'}});
  vi.advanceTimersByTime(151); expect(api.liveBook).toHaveBeenCalledTimes(1);
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

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
    records:vi.fn(()=>of({items:[],total:0,page:1,page_size:25}))};
  TestBed.configureTestingModule({providers:[DeclaratorStore,{provide:DeclaratorApi,useValue:api},
    {provide:BrandingService,useValue:{config:signal({feature_flags:{}})}},
    {provide:AuthStore,useValue:{user:actor,hasPermission:(p:string)=>actor().permissions.includes(p)}},
    {provide:RealtimeService,useValue:{state:signal('open'),on:(...types:string[])=>messages.pipe(filter(m=>types.includes(m.type)),map(m=>m.data))}}]});
  return {store:TestBed.inject(DeclaratorStore),api,requests,messages,actor};
}
const snapshot=(id:string, revision=1)=>({event:{id,status:'live'},matches:[{id:'m',result:{revision}}],current:null}) as DeclaratorMonitor;

describe('Declarator canonical realtime state',()=>{
  beforeEach(()=>vi.useFakeTimers());afterEach(()=>{TestBed.resetTestingModule();vi.useRealTimers();});
  it('discards a late response for a previously selected event',()=>{
    const {store,requests}=setup();store.selectEvent('b');
    requests[1].next(snapshot('b'));requests[0].next(snapshot('a'));
    expect(store.monitor()?.event.id).toBe('b');
  });
  it('coalesces repeated/out-of-order notifications and refetches official history',()=>{
    const {store,api,requests,messages}=setup();requests[0].next(snapshot('a',2));
    for(const revision of [2,1,2]) messages.next({type:'match.result_corrected',data:{revision}});
    vi.advanceTimersByTime(151);
    expect(api.monitor).toHaveBeenCalledTimes(2);
    expect(store.monitor()?.matches[0].result?.revision).toBe(2);
    requests[1].next(snapshot('a',2));expect(store.monitor()?.matches[0].result?.revision).toBe(2);
  });
  it('reloads assignments and canonical state on WebSocket reconnection',()=>{
    const {api,messages}=setup();messages.next({type:'connected',data:{}});
    expect(api.events).toHaveBeenCalledTimes(2);expect(api.monitor).toHaveBeenCalledTimes(2);
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
    expect(store.error()).toBe('Declarator access required');
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

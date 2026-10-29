import { TestBed } from '@angular/core/testing';
import { DatePipe } from '@angular/common';
import { NO_ERRORS_SCHEMA } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { of, throwError } from 'rxjs';
import { ConfirmService } from '@shared/components';
import { ApiError } from '@shared/services';
import { DeclaratorDashboard } from './dashboard';
import { DeclaratorStore } from './store';

function setup(answer: Promise<string|null> = Promise.resolve('')) {
  const record = {match:{id:'fight-135',match_number:135,status:'in_progress'},event_name:'Event'};
  const store = {current:vi.fn(()=>record),canSubmit:()=>true,api:{submit:vi.fn((_id:string, _body:{request_id:string})=>of({status:'pending_approval'}))},refresh:vi.fn(),reconcile:vi.fn()};
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
    expect(confirm.ask).toHaveBeenCalledWith(expect.objectContaining({confirmText:'Confirm Declaration',message:expect.stringContaining('#135')}));
    expect(store.api.submit).toHaveBeenCalledWith('fight-135',expect.objectContaining({outcome,base_revision:0,request_id:expect.any(String)}));
    expect(component.message()).toContain('Awaiting approval');expect(store.refresh).toHaveBeenCalledOnce();
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
    {submitter:true,approver:false,title:'Declare result',declare:true,review:false},
    {submitter:false,approver:true,title:'Result verification',declare:false,review:true},
    {submitter:false,approver:false,title:'Declaration access',declare:false,review:false},
    {submitter:true,approver:true,title:'Declare result',declare:true,review:false},
  ])('renders the permitted controls for $title ($submitter/$approver)', permission => {
    TestBed.configureTestingModule({providers:[
      {provide:ConfirmService,useValue:{ask:vi.fn()}},
      {provide:MatDialog,useValue:{open:vi.fn()}},
    ]});
    const store = {
      submitter:()=>permission.submitter,approver:()=>permission.approver,
      auth:{user:()=>({username:'declarator'})},realtime:{state:()=> 'open'},liveEnabled:()=>true,
      eventId:()=>null,events:()=>[],monitor:()=>null,current:()=>null,loading:()=>false,
      error:()=>null,approvalNotice:()=>'',canSubmit:()=>false,
      records:{rows:()=>[],loading:()=>false,error:()=>null,total:()=>0,page:()=>1,pageSize:()=>25,filter:()=>null},
    };
    // Keep the real dashboard template; child widgets are outside this control-visibility test.
    TestBed.overrideComponent(DeclaratorDashboard,{set:{imports:[DatePipe],schemas:[NO_ERRORS_SCHEMA],
      providers:[{provide:DeclaratorStore,useValue:store}]}});
    const fixture=TestBed.createComponent(DeclaratorDashboard);fixture.detectChanges();
    const text=fixture.nativeElement.textContent as string;
    expect(text).toContain(permission.title);
    expect(text.includes('WINS')).toBe(permission.declare);
    expect(text.includes('Review current declaration')).toBe(permission.review);
    expect(text.includes('check your Declarator account type and role')).toBe(!permission.submitter && !permission.approver);
  });
});

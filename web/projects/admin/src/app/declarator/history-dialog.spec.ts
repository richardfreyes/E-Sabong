import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { EMPTY, Subject, of } from 'rxjs';
import { ConfirmService } from '@shared/components';
import { AuthStore, DeclaratorApi, DeclarationHistory, RealtimeService } from '@shared/services';
import { DeclarationHistoryDialog } from './history-dialog';

const initial = {record:{match:{status:'completed',result:{revision:1}}},declarations:[],audit:[],revisions:[]} as unknown as DeclarationHistory;
function setup() {
  const api={history:vi.fn(()=>of(initial))};
  TestBed.configureTestingModule({providers:[{provide:DeclaratorApi,useValue:api},
    {provide:AuthStore,useValue:{user:signal({account_type:'declarator'}),hasPermission:()=>true}},
    {provide:RealtimeService,useValue:{on:()=>EMPTY}},
    {provide:ConfirmService,useValue:{ask:vi.fn()}},{provide:MAT_DIALOG_DATA,useValue:{matchId:'m'}},
    {provide:MatDialogRef,useValue:{close:vi.fn()}}]});
  TestBed.overrideComponent(DeclarationHistoryDialog,{set:{template:'',imports:[]}});
  return {component:TestBed.createComponent(DeclarationHistoryDialog).componentInstance,api};
}

describe('Canonical result detail actions',()=>{
  it('blocks changes after an uncertain request or denied detail refresh',()=>{
    const {component}=setup();
    expect(component.canAmend()).toBe(true);
    component.error.set('The request could not be verified');
    expect(component.canAmend()).toBe(false);expect(component.active()).toBe(false);
  });
  it('keeps changes disabled while refreshing an older result snapshot',()=>{
    const {component,api}=setup();
    const response=new Subject<DeclarationHistory>();api.history.mockReturnValue(response);
    component.load();expect(component.loading()).toBe(true);expect(component.canAmend()).toBe(false);
    response.next(initial);expect(component.loading()).toBe(false);expect(component.canAmend()).toBe(true);
  });
});

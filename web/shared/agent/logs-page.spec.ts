import { TestBed } from '@angular/core/testing';
import { of, Subject } from 'rxjs';
import { CommissionApi, PortalApi, RealtimeService } from '@shared/services';
import { AgentLogsPage } from './logs-page';

describe('Agent wallet transaction history',()=>{
  it('shows the caller ledger separately from earnings moves and reloads on balance updates',()=>{
    const updates=new Subject<void>();
    const portal={arenaPointsHistory:vi.fn(()=>of({items:[{id:'w1',created_at:'2026-11-13T00:00:00Z',kind:'transfer',memo:'Authorized transfer',delta:'100.00',balance_after:'100.00'}],total:1}))};
    TestBed.configureTestingModule({providers:[
      {provide:CommissionApi,useValue:{balanceRequests:()=>of([]),earningsMoves:()=>of([])}},
      {provide:PortalApi,useValue:portal},{provide:RealtimeService,useValue:{on:()=>updates}},
    ]});
    const fixture=TestBed.createComponent(AgentLogsPage);fixture.detectChanges();
    const region=fixture.nativeElement.querySelector('[aria-label="Main Wallet transaction history"]') as HTMLElement;
    expect(region.textContent).toContain('Authorized transfer');expect(region.textContent).toContain('Balance 100.00');
    expect(portal.arenaPointsHistory).toHaveBeenCalledWith({page:1,page_size:25});
    updates.next();expect(portal.arenaPointsHistory).toHaveBeenCalledTimes(2);
  });
});

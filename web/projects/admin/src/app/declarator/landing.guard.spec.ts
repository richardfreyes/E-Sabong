import { TestBed } from '@angular/core/testing';
import { ActivatedRouteSnapshot, Router, RouterStateSnapshot, UrlTree, provideRouter } from '@angular/router';
import { AuthStore, BrandingService } from '@shared/services';
import { declaratorLandingGuard } from './landing.guard';

function run(type:string, modules:string[], permission=true) {
  TestBed.configureTestingModule({providers:[provideRouter([]),
    {provide:AuthStore,useValue:{user:()=>({account_type:type}),hasAnyPermission:()=>permission}},
    {provide:BrandingService,useValue:{hasModule:(name:string)=>modules.includes(name)}}]});
  return TestBed.runInInjectionContext(()=>declaratorLandingGuard({} as ActivatedRouteSnapshot,{} as RouterStateSnapshot));
}
describe('Declarator landing route',()=>{
  it('opens the dedicated workspace for an enabled authorized Declarator',()=>{
    const result=run('declarator',['events','matches']) as UrlTree;
    expect(TestBed.inject(Router).serializeUrl(result)).toBe('/declarator');
  });
  it('ends at the forbidden route when a required module is disabled, avoiding a redirect loop',()=>{
    const result=run('declarator',['events']) as UrlTree;
    expect(TestBed.inject(Router).serializeUrl(result)).toBe('/forbidden');
  });
  it('keeps other staff on their existing dashboard',()=>{
    expect(run('operator',['events','matches'])).toBe(true);
  });
});

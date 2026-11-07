import { TestBed } from '@angular/core/testing';
import { ActivatedRouteSnapshot, Router, RouterStateSnapshot, UrlTree, provideRouter } from '@angular/router';
import { AuthStore } from '@shared/services';

import { keepStreamOperatorsOut, operatorGuard } from './guards';

function setup(type: string, permissions: string[]): void {
  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      { provide: AuthStore, useValue: { user: () => ({ account_type: type }), hasPermission: (p: string) => permissions.includes(p) } },
    ],
  });
}
const route = {} as ActivatedRouteSnapshot;
const state = {} as RouterStateSnapshot;
const url = (result: unknown) => TestBed.inject(Router).serializeUrl(result as UrlTree);

describe('Stream Operator routing', () => {
  it('sends Stream Operators from every administration URL to their console', () => {
    setup('stream_operator', ['streams.operate']);
    expect(url(TestBed.runInInjectionContext(() => keepStreamOperatorsOut(route, state)))).toBe('/operator');
  });

  it('leaves administrators, event operators and declarators in administration', () => {
    for (const type of ['admin', 'operator', 'declarator', 'agent']) {
      TestBed.resetTestingModule();
      setup(type, []);
      expect(TestBed.runInInjectionContext(() => keepStreamOperatorsOut(route, state))).toBe(true);
    }
  });

  it('opens the console only with streams.operate', () => {
    setup('stream_operator', ['streams.operate']);
    expect(TestBed.runInInjectionContext(() => operatorGuard(route, state))).toBe(true);
    TestBed.resetTestingModule();
    setup('operator', ['events.manage']);
    expect(url(TestBed.runInInjectionContext(() => operatorGuard(route, state)))).toBe('/forbidden');
  });
});

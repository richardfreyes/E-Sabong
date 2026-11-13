import { TestBed } from '@angular/core/testing';
import { Router, UrlTree, provideRouter } from '@angular/router';

import { AuthStore } from '@shared/services';
import { agentHomeMatch, agentOnlyGuard, playerHomeGuard, subAgentsOnlyGuard } from './access';

function setup(type: string): void {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [provideRouter([]), { provide: AuthStore, useValue: { user: () => ({ account_type: type }) } }],
  });
}

const url = (result: unknown) => TestBed.inject(Router).serializeUrl(result as UrlTree);

describe('Agent routing', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('opens the agent dashboard for a master agent and an agent', () => {
    for (const type of ['master_agent', 'agent', 'sub_agent']) {
      setup(type);
      expect(TestBed.runInInjectionContext(() => agentHomeMatch({} as never, [], {} as never))).toBe(true);
      expect(url(TestBed.runInInjectionContext(() => playerHomeGuard({} as never, {} as never)))).toBe('/agent');
      expect(TestBed.runInInjectionContext(() => agentOnlyGuard({} as never, {} as never))).toBe(true);
    }
  });

  it('allows only Agents to distribute commission through Sub-Agent management',()=>{
    for(const role of ['master_agent','sub_agent','player','admin']) {
      setup(role);
      expect(url(TestBed.runInInjectionContext(()=>subAgentsOnlyGuard({} as never,{} as never)))).toBe('/');
    }
    setup('agent');expect(TestBed.runInInjectionContext(()=>subAgentsOnlyGuard({} as never,{} as never))).toBe(true);
  });

  it('leaves administrators, operators, declarators and players on their existing home', () => {
    for (const type of ['admin', 'operator', 'declarator', 'player']) {
      setup(type);
      expect(TestBed.runInInjectionContext(() => agentHomeMatch({} as never, [], {} as never))).toBe(false);
      expect(TestBed.runInInjectionContext(() => playerHomeGuard({} as never, {} as never))).toBe(true);
      expect(url(TestBed.runInInjectionContext(() => agentOnlyGuard({} as never, {} as never)))).toBe('/');
    }
  });
});

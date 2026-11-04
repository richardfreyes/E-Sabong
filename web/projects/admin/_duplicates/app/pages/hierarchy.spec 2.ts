import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { EMPTY, of, Subject } from 'rxjs';
import { AdminApi, BrandingService, RealtimeService } from '@shared/services';
import type { HierarchyNode } from '@shared/models';
import { HierarchyPage } from './hierarchy';

describe('HierarchyPage pending expansions', () => {
  async function setup() {
    const master = { id: 'master', username: 'master1', full_name: 'Master', child_count: 1, status: 'active', account_type: 'master_agent', parent_id: null } as HierarchyNode;
    const agent = { ...master, id: 'agent', username: 'agent1' };
    const children = new Subject<HierarchyNode[]>();
    const api = { hierarchy: vi.fn((id: string | null) => id === 'master' ? of([agent]) : id === 'agent' ? children : of([master])) };
    TestBed.configureTestingModule({ providers: [provideRouter([]),
      { provide: AdminApi, useValue: api },
      { provide: BrandingService, useValue: { hasModule: () => true } },
      { provide: RealtimeService, useValue: { on: () => EMPTY } },
    ] });
    const fixture = TestBed.createComponent(HierarchyPage);
    await fixture.whenStable();
    return { page: fixture.componentInstance, children, api };
  }

  it('does not request the same children twice while expansion is pending', async () => {
    const { page, api } = await setup();
    page.toggle(page.rows()[0]);
    const agent = page.rows()[1];
    page.toggle(agent);
    page.toggle(agent);
    expect(api.hierarchy.mock.calls.filter(([id]) => id === 'agent')).toHaveLength(1);
  });

  it('ignores child results after their ancestor is collapsed', async () => {
    const { page, children } = await setup();
    page.toggle(page.rows()[0]);
    page.toggle(page.rows()[1]);
    page.toggle(page.rows()[0]);
    children.next([{ id: 'late-child', child_count: 0 } as HierarchyNode]);
    expect(page.rows().map(row => row.node.id)).toEqual(['master']);
    expect(page.rows()[0].expanded).toBe(false);
  });
});

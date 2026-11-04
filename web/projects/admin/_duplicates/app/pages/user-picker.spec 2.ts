import { TestBed } from '@angular/core/testing';
import { EMPTY, of, Subject } from 'rxjs';
import type { User } from '@shared/models';
import { AdminApi, RealtimeService } from '@shared/services';
import { UserPickerComponent } from './user-picker';

const user = { id: 'one', username: 'player1', full_name: 'Player', account_type: 'player' } as User;

async function setup() {
  const api = { users: vi.fn((_query?: unknown) => of({ items: [user], total: 1 })), user: vi.fn(() => of(user)) };
  TestBed.configureTestingModule({ providers: [
    { provide: AdminApi, useValue: api }, { provide: RealtimeService, useValue: { on: () => EMPTY } },
  ] });
  const fixture = TestBed.createComponent(UserPickerComponent);
  fixture.componentRef.setInput('accountType', 'player');
  await fixture.whenStable();
  return { fixture, component: fixture.componentInstance, api };
}

describe('Archive account selector', () => {
  it('clears the submitted recipient immediately when selected text is edited', async () => {
    const { component } = await setup();
    const changed = vi.fn();
    component.registerOnChange(changed);
    component.search.setValue(user);
    component.select(user);
    component.search.setValue('different account');
    expect(changed.mock.calls.map(([id]) => id)).toEqual(['one', null]);
    expect(component.selected()).toBeNull();
  });

  it('ignores a delayed account lookup after a new recipient is written', async () => {
    const { component, api } = await setup();
    const pending = new Subject<User>();
    api.user.mockReturnValueOnce(pending).mockReturnValueOnce(of({ ...user, id: 'two' }));
    component.writeValue('one');
    component.writeValue('two');
    pending.next(user);
    expect(pending.observed).toBe(false);
    expect(component.selected()?.id).toBe('two');
  });

  it('passes role filters without transfer eligibility or balances', async () => {
    const { api } = await setup();
    await new Promise(resolve => setTimeout(resolve, 300));
    expect(api.users).toHaveBeenCalledWith(expect.objectContaining({ account_type: 'player' }));
    expect(api.users.mock.calls.at(-1)?.[0]).not.toHaveProperty('transfer_recipients');
    expect(api.users.mock.calls.at(-1)?.[0]).not.toHaveProperty('status');
  });
});

import { Component, DestroyRef, inject, input, output } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { PlayerVideoComponent } from './player-video';

let destroyed = 0;
@Component({selector:'pp-video-stage',template:'<video></video>'})
class FakeStage {
  event=input(null); match=input(null); announcement=input(null); loading=input(false); storeError=input(null);
  refresh=output<void>(); dismiss=output<void>();
  constructor() { inject(DestroyRef).onDestroy(()=>destroyed++); }
}
function setup(balance: string | null) {
  TestBed.overrideComponent(PlayerVideoComponent,{set:{imports:[FakeStage]}});
  const fixture=TestBed.createComponent(PlayerVideoComponent);
  fixture.componentRef.setInput('balance',balance);fixture.detectChanges();
  return {fixture,el:fixture.nativeElement as HTMLElement};
}
describe('Player live-video balance requirement',()=>{
  beforeEach(()=>destroyed=0);
  for (const balance of ['0.00','50.00','99.99']) {
    it(`blocks ${balance} without mounting a video or initiating playback`,()=>{
      const {el}=setup(balance);
      expect(el.querySelector('video')).toBeNull();
      expect(el.textContent).toContain('Insufficient Balance');
      expect(el.textContent).toContain('Please contact your Agent to add balance.');
    });
  }
  for (const balance of ['100.00','150.00','1000.00']) {
    it(`allows ${balance}`,()=>expect(setup(balance).el.querySelector('video')).toBeTruthy());
  }
  it('destroys playback below 100, restores it at 100, and retains the instance while eligible',()=>{
    const {fixture,el}=setup('100.00');
    const initial=el.querySelector('video');
    fixture.componentRef.setInput('balance','150.00');fixture.detectChanges();
    expect(el.querySelector('video')).toBe(initial);
    fixture.componentRef.setInput('balance','99.99');fixture.detectChanges();
    expect(el.querySelector('video')).toBeNull();expect(destroyed).toBe(1);
    fixture.componentRef.setInput('balance','100.00');fixture.detectChanges();
    expect(el.querySelector('video')).toBeTruthy();expect(el.querySelector('video')).not.toBe(initial);
  });
  it('waits for the authoritative wallet before creating the player',()=>{
    const {el}=setup(null);
    expect(el.querySelector('video')).toBeNull();expect(el.textContent).toContain('Checking Balance');
  });
});

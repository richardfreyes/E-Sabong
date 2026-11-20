import { Component, DestroyRef, inject, input, output } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { PlayerVideoComponent, streamAccess } from './player-video';

let destroyed = 0;
@Component({selector:'pp-video-stage',template:'<video></video>'})
class FakeStage {
  globalPlayback=input(false); requireMatch=input(true);
  event=input(null); match=input(null); announcement=input(null); loading=input(false); storeError=input(null);
  refresh=output<void>(); dismiss=output<void>();
  constructor() { inject(DestroyRef).onDestroy(()=>destroyed++); }
}
function setup(balance: string | null, openStakes: string | null = '0.00', accessError = false) {
  TestBed.overrideComponent(PlayerVideoComponent,{set:{imports:[FakeStage]}});
  const fixture=TestBed.createComponent(PlayerVideoComponent);
  fixture.componentRef.setInput('balance',balance);
  fixture.componentRef.setInput('openStakes',openStakes);
  fixture.componentRef.setInput('accessError',accessError);
  fixture.detectChanges();
  return {fixture,el:fixture.nativeElement as HTMLElement};
}
function set(fixture: ReturnType<typeof setup>['fixture'], balance: string | null, openStakes: string | null) {
  fixture.componentRef.setInput('balance',balance);fixture.componentRef.setInput('openStakes',openStakes);fixture.detectChanges();
}

describe('streamAccess', () => {
  it.each([
    ['0.00', '1000.00', 'allowed'], // A: everything staked
    ['500.00', '0.00', 'allowed'], // B
    ['0.00', '0.00', 'denied'], // C
    ['100.00', '2000.00', 'allowed'], // D
    ['0.00', '500.00', 'allowed'], // E: balance reached zero by betting
    ['40.00', '60.00', 'allowed'],
    ['40.00', '59.99', 'denied'],
    ['99.99', '0.00', 'denied'],
  ] as const)('balance %s + open stakes %s is %s', (balance, stakes, expected) => {
    expect(streamAccess(balance, stakes, false)).toBe(expected);
  });
  it('starts on a sufficient balance before open stakes are known, and otherwise keeps checking', () => {
    expect(streamAccess('100.00', null, false)).toBe('allowed');
    expect(streamAccess('0.00', null, false)).toBe('checking');
    expect(streamAccess(null, null, false)).toBe('checking');
  });
  it('reports a failed check instead of checking forever, but never over known figures', () => {
    expect(streamAccess(null, null, true)).toBe('error');
    expect(streamAccess('0.00', '500.00', true)).toBe('allowed');
    expect(streamAccess('0.00', '0.00', true)).toBe('denied');
  });
});

describe('Player live-video access',()=>{
  beforeEach(()=>destroyed=0);
  for (const balance of ['0.00','50.00','99.99']) {
    it(`blocks ${balance} without open stakes, without mounting a video or initiating playback`,()=>{
      const {el}=setup(balance);
      expect(el.querySelector('video')).toBeNull();
      expect(el.textContent).toContain('Insufficient Balance');
      expect(el.textContent).toContain('Please contact your Agent to add balance.');
    });
  }
  for (const balance of ['100.00','150.00','1000.00']) {
    it(`allows ${balance}`,()=>expect(setup(balance).el.querySelector('video')).toBeTruthy());
  }
  it('allows a zero balance with an open bet and never shows insufficient balance',()=>{
    const {el}=setup('0.00','500.00');
    expect(el.querySelector('video')).toBeTruthy();
    expect(el.textContent).not.toContain('Insufficient Balance');
  });
  it('keeps the same player while a bet moves the balance into open stakes',()=>{
    const {fixture,el}=setup('150.00','0.00');
    const initial=el.querySelector('video');
    set(fixture,'0.00','150.00');
    expect(el.querySelector('video')).toBe(initial);expect(destroyed).toBe(0);
    // A grant frame does not resend open stakes; the store keeps them, the player stays.
    set(fixture,'50.00','150.00');
    expect(el.querySelector('video')).toBe(initial);
  });
  it('ends playback when a losing settlement leaves too little, and restores it when topped up',()=>{
    const {fixture,el}=setup('0.00','500.00');
    const initial=el.querySelector('video');
    set(fixture,'0.00','0.00');
    expect(el.querySelector('video')).toBeNull();expect(destroyed).toBe(1);
    expect(el.textContent).toContain('Insufficient Balance');
    set(fixture,'100.00','0.00');
    expect(el.querySelector('video')).toBeTruthy();expect(el.querySelector('video')).not.toBe(initial);
  });
  it('keeps playback through a winning or refunded settlement',()=>{
    const {fixture,el}=setup('0.00','500.00');
    const initial=el.querySelector('video');
    set(fixture,'915.00','0.00');
    expect(el.querySelector('video')).toBe(initial);expect(destroyed).toBe(0);
  });
  it('waits for the authoritative wallet before creating the player',()=>{
    const {el}=setup(null,null);
    expect(el.querySelector('video')).toBeNull();
    expect(el.textContent).toContain('Checking Live Stream Access');
    expect(el.textContent).not.toContain('Checking Balance');
  });
  it('offers a retry when the wallet could not be read',()=>{
    const {fixture,el}=setup(null,null,true);
    let retried=0;fixture.componentInstance.retry.subscribe(()=>retried++);
    expect(el.textContent).toContain("Couldn't Check Live Stream Access");
    el.querySelector('button')!.click();
    expect(retried).toBe(1);
  });
});

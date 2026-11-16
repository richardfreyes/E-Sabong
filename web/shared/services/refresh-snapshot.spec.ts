import { Observable, Subject } from 'rxjs';
import { refreshSnapshot, trailingExhaustMap } from './refresh-snapshot';

describe('bounded snapshot refreshes', () => {
  it('finishes slow reads under continuous traffic, then performs one trailing read', () => {
    const trigger = new Subject<void>(), requests: Subject<number>[] = [], values: number[] = [];
    const subscription = trigger.pipe(trailingExhaustMap(() => {
      const response = new Subject<number>(); requests.push(response); return response;
    })).subscribe(v => values.push(v));
    trigger.next();
    for (let i = 0; i < 10000; i++) trigger.next();
    expect(requests).toHaveLength(1);
    expect(requests[0].observed).toBe(true);
    requests[0].next(1); requests[0].complete();
    expect(requests).toHaveLength(2);
    requests[1].next(2); requests[1].complete();
    expect(values).toEqual([1, 2]);
    subscription.unsubscribe();
  });
  it('cancels old scope reads and tears down sources and in-flight reads', () => {
    const trigger = new Subject<void>(), requests: Subject<string>[] = [], values: string[] = [];
    let key = 'a';
    const sub = refreshSnapshot(trigger, () => key, () => {
      const response = new Subject<string>(); requests.push(response); return response;
    }).subscribe(value => values.push(value));
    trigger.next(); key = 'b'; trigger.next();
    expect(requests[0].observed).toBe(false);
    requests[0].next('old'); requests[1].next('new');
    expect(values).toEqual(['new']);
    sub.unsubscribe();
    expect(trigger.observed).toBe(false); expect(requests[1].observed).toBe(false);
  });
  it('keeps one request and no retained completed subscriptions over 10800 ticks', () => {
    const trigger = new Subject<void>(); let reads = 0, active = 0, peak = 0;
    const sub = trigger.pipe(trailingExhaustMap(() => new Observable<number>(s => {
      active++; peak = Math.max(peak, active); s.next(++reads); s.complete();
      return () => { active--; };
    }))).subscribe();
    for (let i = 0; i < 10800; i++) trigger.next();
    expect(reads).toBe(10800); expect(peak).toBe(1); expect(active).toBe(0);
    sub.unsubscribe(); expect(trigger.observed).toBe(false);
  });
});

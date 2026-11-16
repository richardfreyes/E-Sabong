import { Observable, OperatorFunction, Subscription, distinctUntilChanged, map, startWith, switchMap } from 'rxjs';

/** One read in flight and one dirty bit. A busy stream cannot starve or queue reads. */
export function trailingExhaustMap<T>(read: () => Observable<T>): OperatorFunction<unknown, T> {
  return source => new Observable<T>(subscriber => {
    const subscriptions = new Subscription();
    let active = false, dirty = false, ended = false;
    const run = () => {
      if (active) { dirty = true; return; }
      active = true;
      subscriptions.add(read().subscribe({
        next: value => subscriber.next(value),
        error: error => subscriber.error(error),
        complete: () => {
          active = false;
          if (dirty) { dirty = false; run(); }
          else if (ended) subscriber.complete();
        },
      }));
    };
    subscriptions.add(source.subscribe({
      next: run, error: error => subscriber.error(error),
      complete: () => { ended = true; if (!active) subscriber.complete(); },
    }));
    return subscriptions;
  });
}

/** Cancel immediately on scope changes; coalesce same-scope invalidations while reading. */
export function refreshSnapshot<K, T>(source: Observable<unknown>, key: () => K, read: (key: K) => Observable<T>): Observable<T> {
  return source.pipe(map(key), distinctUntilChanged(), switchMap(scope =>
    source.pipe(startWith(undefined), trailingExhaustMap(() => read(scope))),
  ));
}

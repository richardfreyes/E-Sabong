import { GhostClick, GhostOutcome, GhostQueue } from './ghost-queue';

type Deferred = { resolve: () => void; reject: (e: unknown) => void };

function harness(maxInFlight = 2, capacity = 64) {
  const sent: GhostClick[] = [];
  const replies: Deferred[] = [];
  const outcomes: [number, GhostOutcome][] = [];
  const queue = new GhostQueue(
    {
      send: (click) => {
        sent.push(click);
        return new Promise<void>((resolve, reject) => replies.push({ resolve, reject }));
      },
      settled: (click, outcome) => outcomes.push([click.seq, outcome]),
    },
    maxInFlight,
    capacity,
  );
  const click = (i: number) =>
    queue.enqueue({ matchId: 'm', side: i % 2 ? 'wala' : 'meron', amount: 5000, label: '5K', requestId: 'r' + i });
  return { queue, sent, replies, outcomes, click };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

describe('GhostQueue', () => {
  it("follows the API's bound: a higher one sends waiting clicks now, a lower one lets in-flight requests finish", async () => {
    const { queue, sent, replies, outcomes, click } = harness(2);
    for (let i = 0; i < 8; i++) click(i);
    expect(sent.length).toBe(2);
    queue.setLimit(4);
    expect(sent.length).toBe(4); // two more go out at once, still in click order
    queue.setLimit(1);
    replies[0].resolve(); replies[1].resolve();
    await flush();
    expect(sent.length).toBe(4); // three still in flight, above the new bound: nothing new is sent
    replies[2].resolve(); replies[3].resolve();
    await flush();
    expect(sent.length).toBe(5); // below the bound again: one at a time
    queue.setLimit(0); queue.setLimit(2.5); // invalid bounds are ignored
    expect(queue.maxInFlight).toBe(1);
    expect(sent.map((c) => c.requestId)).toEqual(['r0', 'r1', 'r2', 'r3', 'r4']);
    expect(outcomes.map(([, o]) => o)).toEqual(['accepted', 'accepted', 'accepted', 'accepted']);
  });
  it('is a bounded mergeMap: a slow request never holds back unrelated clicks, answers settle in any order', async () => {
    const { sent, replies, outcomes, click } = harness(6);
    for (let i = 0; i < 4; i++) click(i);
    expect(sent.length).toBe(4); // every click goes out at once while slots are free
    replies[2].resolve(); replies[0].resolve(); // r1 stays unanswered for as long as it takes
    await flush();
    expect(outcomes).toEqual([[3, 'accepted'], [1, 'accepted']]);
    click(4);
    expect(sent.map((c) => c.requestId)).toEqual(['r0', 'r1', 'r2', 'r3', 'r4']);
    expect(new Set(sent.map((c) => c.requestId)).size).toBe(5);
  });
  it('sends in click order with at most two requests in flight', async () => {
    const { queue, sent, replies, click } = harness();
    for (let i = 0; i < 6; i++) click(i);
    expect(sent.map((c) => c.requestId)).toEqual(['r0', 'r1']);
    expect(queue.inFlight()).toBe(2);
    expect(queue.queued()).toBe(4);
    replies[0].resolve();
    await flush();
    expect(sent.map((c) => c.requestId)).toEqual(['r0', 'r1', 'r2']);
    for (let i = 1; i < 6; i++) {
      replies[i].resolve();
      await flush();
    }
    expect(sent.map((c) => c.requestId)).toEqual(['r0', 'r1', 'r2', 'r3', 'r4', 'r5']);
    expect(queue.pending()).toBe(0);
  });

  it('halt cancels and reports every unsent click; in-flight ones are decided by the server', async () => {
    const { queue, sent, replies, outcomes, click } = harness();
    for (let i = 0; i < 32; i++) click(i);
    const cancelled = queue.halt();
    expect(cancelled.length).toBe(30);
    expect(sent.length).toBe(2);
    expect(click(99)).toBeNull(); // new clicks are refused while halted
    replies[0].resolve();
    replies[1].reject(new Error('betting_closed'));
    await flush();
    expect(sent.length).toBe(2); // nothing more is sent after halt
    const byOutcome = (o: GhostOutcome) => outcomes.filter(([, x]) => x === o).length;
    expect(byOutcome('cancelled')).toBe(30);
    expect(byOutcome('accepted')).toBe(1);
    expect(byOutcome('rejected')).toBe(1);
    // Every click ends exactly once.
    expect(new Set(outcomes.map(([seq]) => seq)).size).toBe(32);
    expect(outcomes.length).toBe(32);
  });

  it('never sends a click twice and resumes in order after a Stop', async () => {
    const { queue, sent, replies, click } = harness(2);
    click(0);
    click(1);
    click(2);
    queue.halt();
    replies[0].resolve();
    replies[1].resolve();
    await flush();
    queue.resume();
    click(3);
    click(4);
    await flush();
    expect(sent.map((c) => c.requestId)).toEqual(['r0', 'r1', 'r3', 'r4']);
    expect(new Set(sent.map((c) => c.requestId)).size).toBe(sent.length);
  });

  it('drained resolves once nothing is waiting or in flight', async () => {
    const { queue, replies, click } = harness(2);
    click(0);
    click(1);
    let done = false;
    void queue.drained().then(() => (done = true));
    replies[0].resolve();
    await flush();
    expect(done).toBe(false);
    replies[1].resolve();
    await flush();
    expect(done).toBe(true);
  });

  it('a full queue refuses further clicks instead of growing without bound', () => {
    const { queue, click } = harness(2, 4);
    for (let i = 0; i < 4; i++) expect(click(i)).not.toBeNull();
    expect(queue.canAccept()).toBe(false);
    expect(click(4)).toBeNull();
  });
});

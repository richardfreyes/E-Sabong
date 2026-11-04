import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, Subject } from 'rxjs';
import { AuthStore } from './auth.store';
import { RealtimeService } from './realtime.service';
import { TenantContext } from './tenant-context';

class Socket {
  static CONNECTING = 0;
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => { this.readyState = 3; });
  constructor(_url: string) { Socket.instances.push(this); }
  open() { this.readyState = 1; this.onopen?.(); }
  closed(code = 1000) { this.readyState = 3; this.onclose?.({ code }); }
}

describe('RealtimeService connection ownership', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Socket.instances = [];
    vi.stubGlobal('WebSocket', Socket);
    TestBed.configureTestingModule({ providers: [
      { provide: AuthStore, useValue: { token: signal('demo-token'), refresh: vi.fn(() => of('renewed-token')) } },
      { provide: TenantContext, useValue: { devSlug: () => 'demo' } },
    ] });
  });
  afterEach(() => { TestBed.resetTestingModule(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('ignores late close and message callbacks from a replaced socket', () => {
    const service = TestBed.inject(RealtimeService);
    const received = vi.fn();
    service.messages$.subscribe(received);
    service.connect();
    const old = Socket.instances[0];
    old.open();
    service.disconnect();
    service.connect();
    const current = Socket.instances[1];
    current.open();
    old.closed();
    old.onmessage?.({ data: '{"type":"stale","data":{}}' });
    expect(service.state()).toBe('open');
    expect(received).not.toHaveBeenCalled();
    vi.advanceTimersByTime(25_000);
    expect(current.send).toHaveBeenCalledOnce();
    expect(Socket.instances).toHaveLength(2);
  });

  it('does not reconnect after disconnect while token refresh is pending', () => {
    const refresh = new Subject<string>();
    TestBed.inject(AuthStore).refresh = vi.fn(() => refresh);
    const service = TestBed.inject(RealtimeService);
    service.connect();
    Socket.instances[0].closed(4408);
    service.disconnect();
    refresh.next('renewed');
    vi.advanceTimersByTime(30_000);
    expect(Socket.instances).toHaveLength(1);
    expect(service.state()).toBe('idle');
  });

  it('cancels a pending retry when a connection is opened manually', () => {
    const service = TestBed.inject(RealtimeService);
    service.connect();
    Socket.instances[0].closed();
    service.connect();
    Socket.instances[1].open();
    vi.advanceTimersByTime(2000);
    expect(Socket.instances).toHaveLength(2);
  });
});

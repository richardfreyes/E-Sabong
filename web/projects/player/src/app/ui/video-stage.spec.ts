import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { Match } from '@shared/models';
import { EMPTY } from 'rxjs';
import { ApiClient, RealtimeService } from '@shared/services';
import { PlayerPrefs } from '../core/player-prefs';
import { DEMO_CLIP, VideoStageComponent } from './video-stage';

/** Player-visible recovery states and the demonstration-only playback boundary. */
describe('VideoStageComponent', () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    TestBed.configureTestingModule({ providers: [
      { provide: ApiClient, useValue: {} },
      { provide: RealtimeService, useValue: { state: signal('open'), on: () => EMPTY } },
      { provide: PlayerPrefs, useValue: { videoMuted: () => true, videoVolume: () => 0.8, liveUpdates: () => true, update: vi.fn(), videoUrl: () => 'https://unapproved.example/video.mp4' } },
    ] });
  });
  afterEach(() => vi.restoreAllMocks());

  function setup() {
    const fixture = TestBed.createComponent(VideoStageComponent);
    fixture.componentRef.setInput('playbackSource', {type:'mp4',url:DEMO_CLIP});
    fixture.componentRef.setInput('match', { id: 'match-1', match_number: 135, status: 'in_progress', result: null } as Match);
    fixture.detectChanges();
    return { fixture, component: fixture.componentInstance, video: fixture.nativeElement.querySelector('video') as HTMLVideoElement };
  }

  it('only loads the bundled demonstration clip and shows buffering after playback begins', () => {
    const { fixture, component, video } = setup();
    expect(video.getAttribute('src')).toBe(DEMO_CLIP);
    video.dispatchEvent(new Event('loadeddata'));
    fixture.detectChanges();
    expect(component.showVideo()).toBe(true);
    video.dispatchEvent(new Event('waiting'));
    fixture.detectChanges();
    expect(component.stateTitle()).toBe('Loading the video');
    expect(component.showVideo()).toBe(false);
    video.dispatchEvent(new Event('playing'));
    expect(component.state()).toBe('ready');
  });

  it('shows a recoverable error and returns to loading on retry', () => {
    const { fixture, component, video } = setup();
    video.dispatchEvent(new Event('error'));
    fixture.detectChanges();
    expect(component.stateTitle()).toBe("The video didn't load");
    const refresh = vi.fn();
    component.refresh.subscribe(refresh);
    component.retry();
    expect(component.state()).toBe('loading');
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('shows offline state and reloads when connectivity returns', () => {
    const { component } = setup();
    const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    dispatchEvent(new Event('offline'));
    expect(component.stateTitle()).toBe("You're offline");
    online.mockReturnValue(true);
    dispatchEvent(new Event('online'));
    expect(component.state()).toBe('loading');
  });
});

// The same implementation is also served from the existing /admin/ application.
describe('Shared authorized monitor in the Admin portal', () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    TestBed.configureTestingModule({providers:[
      {provide:ApiClient,useValue:{}},
      {provide:RealtimeService,useValue:{state:signal('open'),on:()=>EMPTY}},
      {provide:PlayerPrefs,useValue:{videoMuted: () => true, videoVolume: () => 0.8,liveUpdates:()=>true,update:vi.fn()}},
    ]});
  });
  afterEach(()=>vi.restoreAllMocks());
  it('loads only the bundled clip under the Admin base path',()=>{
    const base=document.createElement('base');base.href='/admin/';document.head.append(base);
    try {
      const fixture=TestBed.createComponent(VideoStageComponent);fixture.componentRef.setInput('playbackSource',{type:'mp4',url:DEMO_CLIP});fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('video').getAttribute('src')).toBe('/admin/media/demo-loop.mp4');
    } finally {base.remove();}
  });
  it('keeps live updates paused when the tenant has disabled them',()=>{
    const fixture=TestBed.createComponent(VideoStageComponent);
    fixture.componentRef.setInput('forceLiveUpdates',true);
    fixture.componentRef.setInput('liveUpdatesAllowed',false);
    fixture.detectChanges();
    expect(fixture.componentInstance.feedState()).toBe('paused');
  });
});

describe('Authorized provider playback', () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    TestBed.configureTestingModule({ providers: [
      {provide: ApiClient, useValue: {}},
      {provide: RealtimeService, useValue: {state: signal('open'), on: () => EMPTY}},
      {provide: PlayerPrefs, useValue: {videoMuted: () => true, videoVolume: () => 0.8, liveUpdates: () => true, update: vi.fn()}},
    ]});
  });
  afterEach(() => vi.restoreAllMocks());
  it('uses explicit MP4 metadata, replaces the source, and cleans up', () => {
    const fixture = TestBed.createComponent(VideoStageComponent);
    fixture.componentRef.setInput('playbackSource',{type:'mp4',url:'https://approved.example/video'});
    fixture.detectChanges();
    const video: HTMLVideoElement = fixture.nativeElement.querySelector('video');
    expect(video.src).toBe('https://approved.example/video');
    fixture.componentRef.setInput('playbackSource',{type:'mp4',url:'https://approved.example/new'});
    fixture.detectChanges();
    expect(video.src).toBe('https://approved.example/new');
    fixture.destroy();
    expect(video.hasAttribute('src')).toBe(false);
  });
  it('keeps playback mounted for equivalent source objects and unrelated fight updates', () => {
    const fixture = TestBed.createComponent(VideoStageComponent);
    fixture.componentRef.setInput('playbackSource', { type: 'mp4', url: DEMO_CLIP });
    fixture.detectChanges();
    const video = fixture.nativeElement.querySelector('video');
    const loads = vi.mocked(HTMLMediaElement.prototype.load).mock.calls.length;
    fixture.componentRef.setInput('playbackSource', { type: 'mp4', url: DEMO_CLIP });
    fixture.componentRef.setInput('match', { id: 'fight', match_number: 4, status: 'in_progress', result: null });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('video')).toBe(video);
    expect(HTMLMediaElement.prototype.load).toHaveBeenCalledTimes(loads);
  });
  it('does not load expired authorization', () => {
    const fixture = TestBed.createComponent(VideoStageComponent);
    fixture.componentRef.setInput('playbackSource',{type:'mp4',url:'https://approved.example/expired',expires_at:'2020-01-01T00:00:00Z'});
    fixture.detectChanges();
    expect(fixture.componentInstance.providerMessage()).toBe('Provider authentication expired');
    expect(fixture.nativeElement.querySelector('video').hasAttribute('src')).toBe(false);
  });
});

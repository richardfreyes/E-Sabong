import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';

import { FightBookComponent } from './fight-book';

describe('FightBookComponent: betting opens only on a started fight', () => {
  const render = (matchStatus: string, book: unknown = null) => {
    TestBed.configureTestingModule({ providers: [provideNoopAnimations()] });
    const fixture = TestBed.createComponent(FightBookComponent);
    fixture.componentRef.setInput('match', { id: 'm', match_number: 4, status: matchStatus, result: null });
    fixture.componentRef.setInput('event', { id: 'e', status: 'live' });
    fixture.componentRef.setInput('book', book);
    fixture.componentRef.setInput('canManage', true);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  };
  afterEach(() => TestBed.resetTestingModule());

  it('a scheduled fight offers no Open button, only the start hint', () => {
    const el = render('scheduled');
    expect(el.textContent).not.toContain('Open betting');
    expect(el.querySelector('[data-testid="start-before-betting"]')?.textContent).toContain('Start the fight');
  });

  it('a started fight offers Open betting', () => {
    const el = render('in_progress');
    expect(el.textContent).toContain('Open betting');
    expect(el.querySelector('[data-testid="start-before-betting"]')).toBeNull();
  });
});

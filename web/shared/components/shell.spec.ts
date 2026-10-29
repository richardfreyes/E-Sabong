import { BreakpointObserver } from '@angular/cdk/layout';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';
import { AppShellComponent } from './shell';

describe('AppShellComponent responsive navigation', () => {
  function setup() {
    localStorage.clear();
    const breakpoint = new BehaviorSubject({ matches: false, breakpoints: {} });
    TestBed.configureTestingModule({ providers: [provideRouter([]), { provide: BreakpointObserver, useValue: { observe: () => breakpoint } }] });
    const fixture = TestBed.createComponent(AppShellComponent);
    fixture.componentRef.setInput('appName', 'Workspace');
    fixture.componentRef.setInput('navItems', [{ label: 'Overview', icon: 'dashboard', route: '/', exact: true }, { label: 'Events', icon: 'event', route: '/events' }]);
    fixture.detectChanges();
    return { fixture, shell: fixture.componentInstance, breakpoint };
  }

  it('collapses the desktop navigation while retaining accessible link names', () => {
    const { fixture, shell } = setup();
    shell.toggleNavigation();
    fixture.detectChanges();
    expect(shell.collapsed()).toBe(true);
    expect(localStorage.getItem('esb.shell.collapsed')).toBe('true');
    expect(fixture.nativeElement.querySelector('.sidebar.compact')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('a[aria-label="Events"]')).not.toBeNull();
  });

  it('uses the mobile drawer without changing the desktop collapse preference', () => {
    const { fixture, shell, breakpoint } = setup();
    breakpoint.next({ matches: true, breakpoints: {} });
    fixture.detectChanges();
    expect(shell.isMobile()).toBe(true);
    expect(shell.drawerOpen()).toBe(false);
    shell.toggleNavigation();
    expect(shell.drawerOpen()).toBe(true);
    expect(shell.collapsed()).toBe(false);
  });
});

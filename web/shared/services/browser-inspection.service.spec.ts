import { TestBed } from '@angular/core/testing';

import workspace from '../../angular.json';
import { environment } from '../config/environment';
import { BrowserInspectionService, INSPECTION_DETERRENTS } from './browser-inspection.service';

const shortcuts: KeyboardEventInit[] = [
  { key: 'F12' },
  ...['I', 'J', 'C'].map(key => ({ key, code: `Key${key}`, ctrlKey: true, shiftKey: true })),
  ...['I', 'J', 'C'].map(key => ({ key, code: `Key${key}`, metaKey: true, altKey: true })),
];

describe('BrowserInspectionService', () => {
  let host: HTMLElement;

  beforeEach(() => {
    host = document.createElement('div');
    document.body.append(host);
  });

  afterEach(() => {
    TestBed.resetTestingModule();
    host.remove();
    document.getSelection()?.removeAllRanges();
  });

  function start(enabled = true): BrowserInspectionService {
    TestBed.configureTestingModule({ providers: [{ provide: INSPECTION_DETERRENTS, useValue: enabled }] });
    const service = TestBed.inject(BrowserInspectionService);
    service.start();
    return service;
  }

  function key(init: KeyboardEventInit, target = host): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(event);
    return event;
  }

  function menu(target = host, init: MouseEventInit = {}): MouseEvent {
    const event = new MouseEvent('contextmenu', { bubbles: true, composed: true, cancelable: true, button: 2, ...init });
    target.dispatchEvent(event);
    return event;
  }

  it.each(shortcuts)('cancels the inspection shortcut %j without stopping propagation', init => {
    start();
    const observer = vi.fn();
    window.addEventListener('keydown', observer, { once: true });
    expect(key(init).defaultPrevented).toBe(true);
    expect(observer).toHaveBeenCalledOnce();
  });

  it('recognizes macOS Option characters by physical key code', () => {
    start();
    expect(key({ key: 'ç', code: 'KeyC', metaKey: true, altKey: true }).defaultPrevented).toBe(true);
  });

  it('does not register any listeners locally and leaves all shortcuts and right-click intact', () => {
    const spy = vi.spyOn(document, 'addEventListener');
    start(false);
    expect(spy).not.toHaveBeenCalled();
    for (const shortcut of shortcuts) expect(key(shortcut).defaultPrevented).toBe(false);
    expect(menu().defaultPrevented).toBe(false);
    spy.mockRestore();
  });

  it('preserves ordinary typing, editing, navigation and unrelated modifiers', () => {
    start();
    for (const init of [
      { key: 'c' }, { key: 'Tab' }, { key: 'Enter' }, { key: 'Escape' }, { key: 'ArrowDown' },
      { key: 'F10', shiftKey: true }, { key: 'ContextMenu' }, { key: 'c', ctrlKey: true },
      { key: 'v', metaKey: true }, { key: 'z', ctrlKey: true }, { key: 'a', ctrlKey: true },
      { key: 'i', ctrlKey: true }, { key: 'i', ctrlKey: true, shiftKey: true, altKey: true },
      { key: 'i', metaKey: true, altKey: true, shiftKey: true }, { key: 'F12', ctrlKey: true },
      { key: 'i', ctrlKey: true, shiftKey: true, isComposing: true },
    ]) expect(key(init).defaultPrevented).toBe(false);
  });

  it('does not claim non-cancelable events or AltGraph input', () => {
    start();
    expect(key({ key: 'F12', cancelable: false }).defaultPrevented).toBe(false);
    const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...shortcuts[1] });
    vi.spyOn(event, 'getModifierState').mockImplementation(modifier => modifier === 'AltGraph');
    host.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });

  it('respects shortcuts handled by application controls first', () => {
    start();
    host.addEventListener('keydown', event => event.preventDefault(), { once: true });
    const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'F12' });
    const cancel = vi.spyOn(event, 'preventDefault');
    host.dispatchEvent(event);
    expect(cancel).toHaveBeenCalledOnce();
  });

  it('deters pointer right-click and macOS Ctrl-click on ordinary page content', () => {
    start();
    expect(menu().defaultPrevented).toBe(true);
    expect(menu(host, { button: 0, ctrlKey: true }).defaultPrevented).toBe(true);
    expect(menu(host, { button: 0 }).defaultPrevented).toBe(false);
    expect(menu(host, { cancelable: false }).defaultPrevented).toBe(false);
  });

  it.each(['input', 'textarea', 'select', 'video', 'audio', 'div contenteditable="true"', 'div role="textbox"', 'div role="searchbox"', 'div role="combobox"'])('preserves the native menu on %s', markup => {
    start();
    const tag = markup.split(' ')[0];
    host.innerHTML = `<${markup}></${tag}>`;
    expect(menu(host.firstElementChild as HTMLElement).defaultPrevented).toBe(false);
  });

  it('preserves editing menus on nested editable content and inside shadow roots', () => {
    start();
    host.innerHTML = '<div contenteditable="true"><span>edit</span></div>';
    expect(menu(host.querySelector('span')!).defaultPrevented).toBe(false);
    const shadow = host.attachShadow({ mode: 'open' });
    shadow.innerHTML = '<input>';
    expect(menu(shadow.querySelector('input')!).defaultPrevented).toBe(false);
  });

  it('preserves copy/lookup menus for selected text', () => {
    start();
    host.textContent = 'Selectable text';
    const range = document.createRange();
    range.selectNodeContents(host);
    document.getSelection()!.addRange(range);
    expect(menu().defaultPrevented).toBe(false);
  });

  it('installs only two listeners once and removes them on application destruction', () => {
    const spy = vi.spyOn(document, 'addEventListener');
    const service = start();
    service.start();
    expect(spy.mock.calls.map(call => call[0])).toEqual(['keydown', 'contextmenu']);
    spy.mockRestore();
    TestBed.resetTestingModule();
    expect(key({ key: 'F12' }).defaultPrevented).toBe(false);
    expect(menu().defaultPrevented).toBe(false);
  });
});

describe('inspection build configuration', () => {
  it('defaults to unrestricted development without consulting the hostname', () => {
    expect(environment.inspectionDeterrents).toBe(false);
    expect(TestBed.inject(INSPECTION_DETERRENTS)).toBe(false);
  });

  for (const [name, project] of Object.entries(workspace.projects)) {
    it(`${name} explicitly enables deterrents only in optimized production/UAT builds`, () => {
      const build = project.architect.build;
      expect(build.defaultConfiguration).toBe('production');
      expect(project.architect.serve.defaultConfiguration).toBe('development');
      expect(build.configurations.development).toMatchObject({ optimization: false, sourceMap: true });
      for (const config of ['production', 'uat'] as const) {
        expect(build.configurations[config]).toMatchObject({
          sourceMap: false,
          optimization: { scripts: true, styles: { minify: true } },
          fileReplacements: [{ replace: 'shared/config/environment.ts', with: `shared/config/environment.${config}.ts` }],
        });
      }
    });
  }
});

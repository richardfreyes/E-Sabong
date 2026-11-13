import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';

import type { PublicTenantConfig } from '@shared/models';
import { AUTH_CONFIG, AuthStore, BrandingService } from '@shared/services';
import { RegisterPage } from './register';

describe('RegisterPage', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login', portal: 'player' } },
      ],
    });
    TestBed.inject(BrandingService).config.set({ allow_self_registration: true, branding: { app_name: 'Arena' } } as unknown as PublicTenantConfig);
  });

  it('requires at least 6 password characters and the points acknowledgement', () => {
    const page = TestBed.createComponent(RegisterPage).componentInstance;
    page.form.setValue({ username: 'newplayer', full_name: 'New Player', email: '', password: 'abc12', ack: false });
    page.submit();
    expect(page.form.controls.password.errors?.['server']).toBe('Password must be at least 6 characters.');
    expect(page.form.controls.ack.valid).toBe(false);
    TestBed.inject(HttpTestingController).expectNone('/api/v1/auth/register');
  });

  it.each(['abcdef', 'ABCDEF', '123456', 'abc123', 'a1b2c3', '@#$%^&'])('accepts the simple password %s', (password) => {
    const page = TestBed.createComponent(RegisterPage).componentInstance;
    page.form.controls.password.setValue(password);
    expect(page.form.controls.password.errors).toBeNull();
  });

  it('registers and signs the player in with the player portal session', async () => {
    const page = TestBed.createComponent(RegisterPage).componentInstance;
    page.form.setValue({ username: 'newplayer', full_name: 'New Player', email: '', password: 'Str0ng!Passw0rd', ack: true });
    page.submit();
    const req = TestBed.inject(HttpTestingController).expectOne('/api/v1/auth/register');
    expect(req.request.body).toEqual({ username: 'newplayer', full_name: 'New Player', email: null, password: 'Str0ng!Passw0rd' });
    // The registration cookie belongs to the player portal, not to a cookie shared with /admin/.
    expect(req.request.headers.get('X-Portal')).toBe('player');
    expect(req.request.withCredentials).toBe(true);
    req.flush({ access_token: 'tok', token_type: 'bearer', expires_in: 900, user: { permissions: [], must_change_password: false } });
    await vi.waitFor(() => expect(TestBed.inject(AuthStore).isAuthenticated()).toBe(true));
  });
});

describe('RegisterPage from a referral link', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login', portal: 'player' } },
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap({ ref: 'MasterAgent' }) } } },
      ],
    });
    TestBed.inject(BrandingService).config.set({ allow_self_registration: false, branding: { app_name: 'Arena' } } as unknown as PublicTenantConfig);
  });

  it('shows the form from a referral link even when open registration is off, and sends the code', () => {
    const fixture = TestBed.createComponent(RegisterPage);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="referrer"]')!.textContent).toContain('MasterAgent');
    const page = fixture.componentInstance;
    page.form.setValue({ username: 'newplayer', full_name: 'New Player', email: '', password: 'abcdef', ack: true });
    page.submit();
    const req = TestBed.inject(HttpTestingController).expectOne('/api/v1/auth/register');
    expect(req.request.body).toEqual({ username: 'newplayer', full_name: 'New Player', email: null, password: 'abcdef', ref: 'MasterAgent' });
  });
});

describe('RegisterPage from an agent application link', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
        { provide: AUTH_CONFIG, useValue: { basePath: '/auth', loginRoute: '/login', portal: 'player' } },
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap({ ref: 'MasterAgent', apply: 'agent' }) } } },
      ],
    });
    TestBed.inject(BrandingService).config.set({ allow_self_registration: false, branding: { app_name: 'Arena' } } as unknown as PublicTenantConfig);
  });

  it('says it is an agent application and sends apply=agent', () => {
    const fixture = TestBed.createComponent(RegisterPage);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    TestBed.inject(HttpTestingController).expectOne(req => req.url === '/api/v1/auth/referral').flush({account_type:'agent'});
    fixture.detectChanges();
    expect(el.textContent).toContain('Apply as an agent');
    const page = fixture.componentInstance;
    page.form.setValue({ username: 'newagent', full_name: 'New Agent', email: '', password: '123456', ack: true });
    page.submit();
    const req = TestBed.inject(HttpTestingController).expectOne('/api/v1/auth/register');
    expect(req.request.body).toEqual(expect.objectContaining({ ref: 'MasterAgent', apply: 'agent' }));
  });
  it('resolves an older Agent invitation to Sub-Agent and submits that role', () => {
    const fixture=TestBed.createComponent(RegisterPage);fixture.detectChanges();
    const http=TestBed.inject(HttpTestingController);
    http.expectOne(req=>req.url==='/api/v1/auth/referral').flush({account_type:'sub_agent',referrer_username:'agent2'});
    fixture.detectChanges();
    const el=fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Apply as a Sub-Agent');
    expect(el.textContent).toContain('a Sub-Agent under this Agent');
    expect(el.textContent).toContain('application from agent2');
    expect(el.textContent).not.toContain('under this Master Agent');
    const page=fixture.componentInstance;
    page.form.setValue({username:'subagent1',full_name:'Test',email:'',password:'123456',ack:true});
    page.submit();
    expect(http.expectOne('/api/v1/auth/register').request.body).toEqual(expect.objectContaining({apply:'sub_agent'}));
  });

  it('cannot submit until the invitation is validated', () => {
    const fixture=TestBed.createComponent(RegisterPage);fixture.detectChanges();
    const page=fixture.componentInstance;
    page.form.setValue({username:'subagent1',full_name:'Test',email:'',password:'123456',ack:true});
    page.submit();
    const http=TestBed.inject(HttpTestingController);http.expectNone('/api/v1/auth/register');
    http.expectOne(req=>req.url==='/api/v1/auth/referral').flush({error:{code:'invalid_referral',message:'Invalid invitation'}},{status:422,statusText:'Unprocessable Entity'});
    page.submit();http.expectNone('/api/v1/auth/register');
    expect(page.error()).toBe('Invalid invitation');
  });

});

import { DEFAULT_BRAND_COLORS } from '@shared/services';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatTabsModule } from '@angular/material/tabs';
import { MatTooltipModule } from '@angular/material/tooltip';

import { ConfirmService, FieldErrorComponent, PageHeaderComponent, StatusChipComponent } from '@shared/components';
import type { Branding, Domain, ModuleKey, TenantSettings } from '@shared/models';
import { AdminApi, AuthStore, BrandingService, ThemeService, ToastService } from '@shared/services';

const MODULES: { key: ModuleKey; label: string; hint: string }[] = [
  { key: 'events', label: 'Events', hint: 'Event calendar in both portals' },
  { key: 'matches', label: 'Matches & results', hint: 'Fight cards and official results' },
  { key: 'points', label: 'Demonstration points', hint: 'Non-monetary practice balances' },
  { key: 'reports', label: 'Reports', hint: 'Activity, points and match reports' },
  { key: 'notifications', label: 'Notifications', hint: 'In-app notifications for players' },
];
const ASSETS: { kind: 'logo' | 'favicon' | 'login_background'; label: string; field: keyof Branding }[] = [
  { kind: 'logo', label: 'Logo', field: 'logo_url' },
  { kind: 'favicon', label: 'Favicon', field: 'favicon_url' },
  { kind: 'login_background', label: 'Sign-in background', field: 'login_background_url' },
];

@Component({
  selector: 'app-admin-settings',
  imports: [ReactiveFormsModule, MatTabsModule, MatFormFieldModule, MatInputModule, MatButtonModule, MatCheckboxModule, MatSlideToggleModule,
            MatIconModule, MatTooltipModule, PageHeaderComponent, StatusChipComponent, FieldErrorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-page-header title="Workspace settings" subtitle="Branding changes apply instantly to both portals — no rebuild required." />
    <mat-tab-group mat-stretch-tabs="false" animationDuration="0ms">
      @if (auth.hasPermission('branding.manage')) {
        <mat-tab label="Branding">
          <div class="grid gap-6 py-4 lg:grid-cols-[1fr_20rem]">
            <form [formGroup]="branding" (ngSubmit)="saveBranding()" class="grid grid-cols-1 gap-x-3 sm:grid-cols-2" novalidate>
              <mat-form-field class="sm:col-span-2"><mat-label>Application name</mat-label><input matInput formControlName="app_name" />
                <mat-error><app-field-error [control]="branding.controls.app_name" /></mat-error></mat-form-field>
              @for (c of colors; track c.key) {
                <mat-form-field><mat-label>{{ c.label }}</mat-label><input matInput [formControlName]="c.key" (input)="preview()" />
                  <input matSuffix type="color" class="mr-2 h-7 w-9 cursor-pointer border-0 bg-transparent" [formControlName]="c.key" (input)="preview()" [attr.aria-label]="c.label + ' picker'" />
                  <mat-error><app-field-error [control]="branding.get(c.key)" /></mat-error></mat-form-field>
              }
              <mat-form-field><mat-label>Sign-in headline</mat-label><input matInput formControlName="login_title" /></mat-form-field>
              <mat-form-field><mat-label>Sign-in subtitle</mat-label><input matInput formControlName="login_subtitle" /></mat-form-field>
              <mat-form-field><mat-label>Contact email</mat-label><input matInput formControlName="contact_email" />
                <mat-error><app-field-error [control]="branding.controls.contact_email" /></mat-error></mat-form-field>
              <mat-form-field><mat-label>Contact phone</mat-label><input matInput formControlName="contact_phone" /></mat-form-field>
              <mat-form-field class="sm:col-span-2"><mat-label>Contact address</mat-label><input matInput formControlName="contact_address" /></mat-form-field>
              <mat-form-field class="sm:col-span-2"><mat-label>Footer text</mat-label><input matInput formControlName="footer_text" /></mat-form-field>
              <div class="flex gap-2 sm:col-span-2"><button mat-flat-button type="submit">Save branding</button>
                <button mat-button type="button" (click)="revert()">Discard preview</button></div>
            </form>
            <aside class="flex flex-col gap-4">
              @for (a of assets; track a.kind) {
                <div class="rounded-xl border border-outline-variant p-3">
                  <p class="m-0 mb-2 text-sm font-medium">{{ a.label }}</p>
                  <div class="flex items-center gap-3">
                    <div class="grid h-16 w-16 place-items-center overflow-hidden rounded-lg bg-surface-container">
                      @if (current()?.[a.field]; as url) { <img [src]="url" alt="" class="max-h-full max-w-full object-contain" /> }
                      @else { <mat-icon class="text-on-surface-variant" aria-hidden="true">image</mat-icon> }
                    </div>
                    <div class="flex flex-col gap-1">
                      <label class="cursor-pointer text-sm text-primary">Upload<input type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/x-icon" class="sr-only" (change)="upload(a.kind, $event)" /></label>
                      @if (current()?.[a.field]) { <button type="button" class="border-0 bg-transparent p-0 text-left text-sm text-error" (click)="removeAsset(a.kind)">Remove</button> }
                    </div>
                  </div>
                </div>
              }
              <p class="m-0 text-xs text-on-surface-variant">PNG, JPEG, WEBP, GIF or ICO up to 2 MB. Images are re-encoded on upload; SVG is not accepted.</p>
            </aside>
          </div>
        </mat-tab>
      }
      @if (auth.hasPermission('settings.manage')) {
        <mat-tab label="General">
          <form [formGroup]="general" (ngSubmit)="saveGeneral()" class="flex max-w-2xl flex-col gap-2 py-4" novalidate>
            <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
              <mat-form-field><mat-label>Timezone</mat-label><input matInput formControlName="timezone" placeholder="Asia/Manila" />
                <mat-hint>IANA name; used for reports</mat-hint></mat-form-field>
              <mat-form-field><mat-label>Locale</mat-label><input matInput formControlName="locale" /></mat-form-field>
            </div>
            <mat-slide-toggle formControlName="allow_self_registration">Allow players to register themselves</mat-slide-toggle>
            <fieldset class="m-0 mt-4 border-0 p-0">
              <legend class="mb-2 text-sm font-semibold">Enabled modules</legend>
              <p class="m-0 mb-2 text-xs text-on-surface-variant">Only modules included in your plan take effect.</p>
              @for (m of modules; track m.key) {
                <mat-checkbox [checked]="enabled().has(m.key)" (change)="toggleModule(m.key, $event.checked)">{{ m.label }}
                  <span class="block text-xs text-on-surface-variant">{{ m.hint }}</span></mat-checkbox>
              }
            </fieldset>
            <div><button mat-flat-button type="submit">Save settings</button></div>
          </form>
        </mat-tab>
        <mat-tab label="Domains">
          <div class="max-w-3xl py-4">
            <form [formGroup]="domainForm" (ngSubmit)="addDomain()" class="mb-4 flex flex-col gap-2 sm:flex-row sm:items-start">
              <mat-form-field class="flex-1" subscriptSizing="dynamic"><mat-label>Custom domain</mat-label><input matInput formControlName="domain" placeholder="play.yourbrand.com" /></mat-form-field>
              <button mat-flat-button type="submit" class="sm:!mt-2">Add</button>
            </form>
            <ul class="m-0 flex list-none flex-col gap-3 p-0">
              @for (d of domains(); track d.id) {
                <li class="rounded-xl border border-outline-variant p-3">
                  <div class="flex flex-wrap items-center gap-2"><span class="font-medium">{{ d.domain }}</span><app-status-chip [status]="d.verification_status" />
                    @if (d.is_primary) { <app-status-chip status="active" text="primary" /> }
                    <span class="flex-1"></span>
                    @if (d.kind === 'custom' && d.verification_status !== 'verified') { <button mat-stroked-button type="button" (click)="verify(d)">Check DNS</button> }
                    @if (d.kind === 'custom') { <button mat-icon-button type="button" (click)="removeDomain(d)" matTooltip="Remove" aria-label="Remove domain"><mat-icon>delete</mat-icon></button> }
                  </div>
                  @if (d.instructions && d.verification_status !== 'verified') {
                    <p class="m-0 mt-2 text-xs text-on-surface-variant">Add these DNS records at your registrar, then check DNS. HTTPS is issued automatically once verified.</p>
                    <div class="mt-1 grid gap-1 rounded-lg bg-surface-container p-2 font-mono text-xs">
                      <span>TXT {{ d.instructions.txt_name }} = "{{ d.instructions.txt_value }}"</span>
                      <span>CNAME {{ d.instructions.cname_name }} → {{ d.instructions.cname_value }}</span>
                    </div>
                  }
                </li>
              }
            </ul>
          </div>
        </mat-tab>
      }
    </mat-tab-group>
  `,
})
export class SettingsPage {
  readonly auth = inject(AuthStore);
  private readonly api = inject(AdminApi);
  private readonly brandingService = inject(BrandingService);
  private readonly theme = inject(ThemeService);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly fb = inject(FormBuilder);
  readonly modules = MODULES;
  readonly assets = ASSETS;
  readonly colors = [
    { key: 'primary_color', label: 'Primary colour' },
    { key: 'secondary_color', label: 'Secondary colour' },
    { key: 'background_color', label: 'Page background' },
    { key: 'surface_color', label: 'Surface colour' },
  ];
  readonly current = signal<Branding | null>(null);
  readonly domains = signal<Domain[]>([]);
  readonly enabled = signal(new Set<ModuleKey>());
  private readonly hex = Validators.pattern(/^#[0-9a-fA-F]{6}$/);
  readonly branding = this.fb.nonNullable.group({
    app_name: ['', [Validators.required, Validators.maxLength(120)]],
    primary_color: [DEFAULT_BRAND_COLORS.primary, [Validators.required, this.hex]],
    secondary_color: [DEFAULT_BRAND_COLORS.secondary, [Validators.required, this.hex]],
    background_color: [DEFAULT_BRAND_COLORS.background, [Validators.required, this.hex]],
    surface_color: [DEFAULT_BRAND_COLORS.surface, [Validators.required, this.hex]],
    login_title: [''],
    login_subtitle: [''],
    contact_email: ['', Validators.email],
    contact_phone: [''],
    contact_address: [''],
    footer_text: [''],
  });
  readonly general = this.fb.nonNullable.group({ timezone: ['Asia/Manila', Validators.required], locale: ['en'], allow_self_registration: [false] });
  readonly domainForm = this.fb.nonNullable.group({ domain: ['', [Validators.required, Validators.minLength(4)]] });

  constructor() {
    if (this.auth.hasPermission('branding.manage')) this.api.branding().subscribe((b) => this.setBranding(b));
    if (this.auth.hasPermission('settings.manage')) {
      this.api.settings().subscribe((s) => this.setSettings(s));
      this.loadDomains();
    }
  }

  private setBranding(b: Branding): void {
    this.current.set(b);
    this.branding.reset(Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v ?? ''])) as never);
  }

  private setSettings(s: TenantSettings): void {
    this.general.reset({ timezone: s.timezone, locale: s.locale, allow_self_registration: s.allow_self_registration });
    this.enabled.set(new Set(s.enabled_modules));
  }

  preview(): void {
    const v = this.branding.getRawValue();
    if ([v.primary_color, v.secondary_color, v.background_color].every((c) => /^#[0-9a-fA-F]{6}$/.test(c))) {
      this.theme.applyBrand({ primary: v.primary_color, secondary: v.secondary_color, background: v.background_color, surface: v.surface_color });
    }
  }

  revert(): void {
    const b = this.current();
    if (b) {
      this.setBranding(b);
      this.theme.applyBrand({ primary: b.primary_color, secondary: b.secondary_color, background: b.background_color, surface: b.surface_color });
    }
  }

  saveBranding(): void {
    if (this.branding.invalid) {
      this.branding.markAllAsTouched();
      return;
    }
    const body = Object.fromEntries(Object.entries(this.branding.getRawValue()).map(([k, v]) => [k, v === '' ? null : v]));
    this.api.saveBranding(body).subscribe({ next: (b) => (this.applied(b), this.toast.success('Branding saved')), error: (e) => this.toast.error(e) });
  }

  upload(kind: 'logo' | 'favicon' | 'login_background', ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    this.api.uploadAsset(kind, file).subscribe({ next: (b) => (this.applied(b), this.toast.success('Image uploaded')), error: (e) => this.toast.error(e) });
  }

  removeAsset(kind: 'logo' | 'favicon' | 'login_background'): void {
    this.api.removeAsset(kind).subscribe({ next: (b) => this.applied(b), error: (e) => this.toast.error(e) });
  }

  private applied(b: Branding): void {
    this.setBranding(b);
    const cfg = this.brandingService.config();
    if (cfg) this.brandingService.apply({ ...cfg, branding: b });
  }

  toggleModule(key: ModuleKey, on: boolean): void {
    const next = new Set(this.enabled());
    if (on) next.add(key);
    else next.delete(key);
    this.enabled.set(next);
    this.general.markAsDirty();
  }

  saveGeneral(): void {
    this.api.saveSettings({ ...this.general.getRawValue(), enabled_modules: [...this.enabled()] }).subscribe({
      next: (s) => {
        this.setSettings(s);
        this.toast.success('Settings saved');
        void this.brandingService.load();
      },
      error: (e) => this.toast.error(e),
    });
  }

  loadDomains(): void {
    this.api.domains().subscribe((d) => this.domains.set(d));
  }

  addDomain(): void {
    if (this.domainForm.invalid) return;
    this.api.addDomain(this.domainForm.getRawValue().domain).subscribe({ next: () => (this.domainForm.reset(), this.loadDomains()), error: (e) => this.toast.error(e) });
  }

  verify(d: Domain): void {
    this.api.verifyDomain(d.id).subscribe({
      next: (r) => (this.toast.info(r.verification_status === 'verified' ? 'Domain verified' : 'TXT record not found yet'), this.loadDomains()),
      error: (e) => this.toast.error(e),
    });
  }

  async removeDomain(d: Domain): Promise<void> {
    if ((await this.confirm.ask({ title: 'Remove domain', message: `Stop serving the workspace at ${d.domain}?`, confirmText: 'Remove', destructive: true })) === null) return;
    this.api.removeDomain(d.id).subscribe({ next: () => this.loadDomains(), error: (e) => this.toast.error(e) });
  }
}

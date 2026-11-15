import { DatePipe, KeyValuePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { PageHeaderComponent } from '@shared/components';
import type { Tenant, Page } from '@shared/models';
import { ApiClient, toApiError } from '@shared/services';

interface Config {
  shared_domain_enabled: boolean;
  default_tenant_id: string | null;
}
interface Summary {
  accounts: Record<string, number>; monthly_cents: number | null; next_billing_at: string | null;
  subscription_status: string | null; player_registration_url: string | null; admin_login: string | null;
}
interface BillingAlert { tenant_id:string; tenant_name:string; number:string; status:string; due_at:string; total_cents:number }
interface Account { id:string;username:string;account_type:string;parent_username:string|null;status:string }

@Component({
  selector: 'app-owner-rental',
  imports: [FormsModule, MatButtonModule, RouterLink, PageHeaderComponent, DatePipe, KeyValuePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [`
    section { padding: 1.5rem; border: 1px solid var(--mat-sys-outline-variant); border-radius: 1rem; margin-bottom: 1rem; }
    .fields { display: grid; grid-template-columns: repeat(auto-fit,minmax(min(100%,240px),1fr)); gap: 1rem; margin: 1rem 0; }
    label { display: flex; flex-direction: column; gap: .5rem; }
    input,select { padding: .75rem; border: 1px solid var(--mat-sys-outline); border-radius: .5rem; background: var(--mat-sys-surface); color: inherit; min-width: 0; }
    .actions { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: 1rem; }
    .fight { border-top: 1px solid var(--mat-sys-outline-variant); padding: 1rem 0; }
    output,a { overflow-wrap: anywhere; }
  `],
  template: `
    <app-page-header title="Tenant Rental" subtitle="Manage tenant access, subscriptions, and monthly billing." />
    @if (error()) { <p role="alert" class="text-error">{{ error() }}</p> }
    @if (notice()) { <p role="status">{{ notice() }}</p> }
    <section>
      <h2>Shared player website</h2>
      <p>All tenant accounts sign in at the platform's main domain. Existing duplicate usernames use username@workspace-code.</p>
      <div class="fields">
        <label>Shared domain access<select [(ngModel)]="config.shared_domain_enabled"><option [ngValue]="false">Disabled (existing host routing)</option><option [ngValue]="true">Enabled</option></select></label>
        <label>Find workspace<input [(ngModel)]="search" (ngModelChange)="loadTenants()" placeholder="Workspace name or code" /></label>
        <label>Default Admin for direct registration<select [(ngModel)]="config.default_tenant_id"><option [ngValue]="null">Require invitation</option>@for (t of tenants(); track t.id) { <option [ngValue]="t.id">{{ t.name }} ({{ t.slug }})</option> }</select></label>
      </div>
      <button mat-flat-button [disabled]="busy()" (click)="saveConfig()">Save Changes</button>
    </section>
    <section>
      <h2>Monthly rental</h2>
      @for (alert of alerts(); track alert.number) { <p role="status">{{ alert.tenant_name }} · {{ alert.number }} · {{ alert.status }} · due {{ alert.due_at | date:'mediumDate' }}</p> }
      <div class="fields">
        <label>Tenant<select [(ngModel)]="tenantId" [disabled]="busy()" (ngModelChange)="loadSummary()"><option value="">Select tenant</option>@for (t of tenants(); track t.id) { <option [value]="t.id">{{ t.name }} · {{ t.status }}</option> }</select></label>
        <label>Monthly rental (PHP)<input type="number" min="0" step="0.01" [(ngModel)]="monthly" /></label>
        <label>Next billing date (optional)<input type="datetime-local" [(ngModel)]="nextBilling" /></label>
        <label>Activation date (optional)<input type="datetime-local" [(ngModel)]="activationAt" /></label>
      </div>
      <div class="actions">
        <button mat-flat-button [disabled]="busy() || loadingTenant() || !tenantId" (click)="savePrice()">Save rental fee</button>
        <button mat-stroked-button [disabled]="busy() || !tenantId" (click)="status('suspend')">Suspend tenant</button>
        <button mat-stroked-button [disabled]="busy() || !tenantId" (click)="status('activate')">Reactivate tenant</button>
        <a mat-button routerLink="/invoices">Invoices & payment history</a><a mat-button routerLink="/tenants">Create or manage tenants</a>
      </div>
      @if (summary(); as s) {
        <p>Subscription: {{ s.subscription_status ?? 'No subscription' }} · Next billing: {{ s.next_billing_at | date:'medium' }}</p>
        <p>Admin login: {{ s.admin_login }}</p>
        <p>Player registration: <a [href]="s.player_registration_url">{{ s.player_registration_url }}</a></p>
        @for (count of s.accounts | keyvalue; track count.key) { <p>{{ count.key }}: {{ count.value }}</p> }
        <label>Find account<input [(ngModel)]="accountSearch" (ngModelChange)="accountPage=1; loadAccounts()" placeholder="Username" /></label>
        @for (account of accounts(); track account.id) { <p>{{ account.username }} · {{ account.account_type }} · parent: {{ account.parent_username || 'Parent Admin' }} · {{ account.status }}</p> }
        <div class="actions"><button mat-button [disabled]="accountPage <= 1" (click)="accountPage=accountPage-1; loadAccounts()">Previous accounts</button><button mat-button [disabled]="accountPage*25 >= accountTotal()" (click)="accountPage=accountPage+1; loadAccounts()">Next accounts</button></div>
      }
    </section>
  `,
})
export class RentalPage {
  private readonly api = inject(ApiClient);
  readonly busy = signal(false); readonly error = signal(''); readonly notice = signal('');
  readonly tenants = signal<Tenant[]>([]);
  readonly summary = signal<Summary | null>(null);
  readonly alerts = signal<BillingAlert[]>([]);
  readonly accounts = signal<Account[]>([]); readonly accountTotal = signal(0);
  readonly loadingTenant = signal(false);
  private tenantRequest = 0; private accountRequest = 0;
  accountSearch=''; accountPage=1;
  config: Config = {shared_domain_enabled:false,default_tenant_id:null};
  search = ''; tenantId = ''; monthly = 0; nextBilling = ''; activationAt = '';
  constructor() { void this.initialize(); }
  private async initialize() {
    await this.run(async () => { this.config = await firstValueFrom(this.api.get<Config>('/platform/rental/settings')); this.alerts.set(await firstValueFrom(this.api.get<BillingAlert[]>('/platform/rental/alerts'))); await this.loadTenants(); }, '');
  }
  async loadTenants() {
    try { this.tenants.set((await firstValueFrom(this.api.get<Page<Tenant>>('/platform/tenants', {q:this.search,page_size:100}))).items); }
    catch(e) { this.error.set(toApiError(e).message); }
  }
  async saveConfig() { await this.run(async () => {
    this.config = await firstValueFrom(this.api.put<Config>('/platform/rental/settings', {
      shared_domain_enabled: this.config.shared_domain_enabled, default_tenant_id: this.config.default_tenant_id,
    }));
  }); }
  async loadSummary() {
    const id=this.tenantId, request=++this.tenantRequest;
    this.summary.set(null); this.accounts.set([]); this.monthly=Number.NaN; this.loadingTenant.set(!!id);
    if (!id) return;
    try { const s = await firstValueFrom(this.api.get<Summary>(`/platform/rental/tenants/${id}/summary`));
      if (request !== this.tenantRequest || id !== this.tenantId) return;
      this.summary.set(s); this.monthly = (s.monthly_cents ?? 0)/100; this.nextBilling = this.activationAt = this.accountSearch = ''; this.accountPage=1; await this.loadAccounts();
    } catch(e) { if(request === this.tenantRequest) this.error.set(toApiError(e).message); }
    finally { if(request === this.tenantRequest) this.loadingTenant.set(false); }
  }
  async loadAccounts() {
    const id=this.tenantId, request=++this.accountRequest;
    try { const page = await firstValueFrom(this.api.get<Page<Account>>(`/platform/rental/tenants/${id}/accounts`,{q:this.accountSearch,page:this.accountPage,page_size:25})); if(request !== this.accountRequest || id !== this.tenantId) return; this.accounts.set(page.items); this.accountTotal.set(page.total); }
    catch(e) { if(request === this.accountRequest) this.error.set(toApiError(e).message); }
  }
  async savePrice() {
    if(this.loadingTenant() || !this.tenantId) return;
    if (!Number.isFinite(this.monthly) || this.monthly < 0) { this.error.set('Enter a non-negative rental fee'); return; }
    await this.run(async () => { await firstValueFrom(this.api.put(`/platform/rental/tenants/${this.tenantId}/price`, {monthly_cents:Math.round(this.monthly*100),next_billing_at:this.nextBilling ? new Date(this.nextBilling).toISOString() : null,activation_at:this.activationAt ? new Date(this.activationAt).toISOString() : null})); await this.loadSummary(); });
  }
  async status(action: string) { await this.run(async () => { await firstValueFrom(this.api.post(`/platform/tenants/${this.tenantId}/${action}`, action === 'suspend' ? {reason:'Suspended by OWNER'} : {})); await this.loadTenants(); }); }
  private async run(action:()=>Promise<void>,message='Changes saved') {
    if (this.busy()) return;
    this.busy.set(true); this.error.set(''); this.notice.set('');
    try { await action(); this.notice.set(message); } catch(e) { this.error.set(toApiError(e).message); } finally { this.busy.set(false); }
  }
}

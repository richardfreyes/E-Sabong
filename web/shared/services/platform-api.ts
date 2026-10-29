import { Injectable, inject } from '@angular/core';

import type {
  AuditEntry,
  BillingCycle,
  Branding,
  Deployment,
  Domain,
  FeatureFlag,
  Invoice,
  License,
  Message,
  Page,
  PageQuery,
  Plan,
  PlatformOverview,
  SignupRequest,
  Subscription,
  Tenant,
  TenantDetail,
  TenantFlagState,
  TenantProvisioned,
  UsagePoint,
  Uuid,
} from '@shared/models';
import { ApiClient } from './api-client';

/** SaaS owner (control plane) endpoints. */
@Injectable({ providedIn: 'root' })
export class PlatformApi {
  private readonly api = inject(ApiClient);

  overview = () => this.api.get<PlatformOverview>('/platform/analytics/overview');

  // tenants
  tenants = (q: PageQuery) => this.api.get<Page<Tenant>>('/platform/tenants', q);
  tenant = (id: Uuid) => this.api.get<TenantDetail>(`/platform/tenants/${id}`);
  createTenant = (body: Record<string, unknown>) => this.api.post<TenantProvisioned>('/platform/tenants', body);
  updateTenant = (id: Uuid, body: { name: string }) => this.api.patch<Tenant>(`/platform/tenants/${id}`, body);
  suspendTenant = (id: Uuid, reason: string | null) => this.api.post<Tenant>(`/platform/tenants/${id}/suspend`, { reason });
  activateTenant = (id: Uuid) => this.api.post<Tenant>(`/platform/tenants/${id}/activate`, {});
  deleteTenant = (id: Uuid) => this.api.delete<Message>(`/platform/tenants/${id}`);
  usage = (id: Uuid, days = 30) => this.api.get<UsagePoint[]>(`/platform/tenants/${id}/usage`, { days });

  // domains & branding
  addDomain = (tenantId: Uuid, domain: string) => this.api.post<Domain>(`/platform/tenants/${tenantId}/domains`, { domain });
  verifyDomain = (id: Uuid) => this.api.post<Domain>(`/platform/domains/${id}/verify`);
  primaryDomain = (id: Uuid) => this.api.post<Domain>(`/platform/domains/${id}/primary`);
  removeDomain = (id: Uuid) => this.api.delete<Message>(`/platform/domains/${id}`);
  updateBranding = (tenantId: Uuid, body: Partial<Branding>) => this.api.put<Branding>(`/platform/tenants/${tenantId}/branding`, body);

  // flags
  flags = () => this.api.get<FeatureFlag[]>('/platform/feature-flags');
  createFlag = (body: Partial<FeatureFlag>) => this.api.post<FeatureFlag>('/platform/feature-flags', body);
  updateFlag = (id: Uuid, body: Partial<FeatureFlag>) => this.api.patch<FeatureFlag>(`/platform/feature-flags/${id}`, body);
  tenantFlags = (tenantId: Uuid) => this.api.get<TenantFlagState[]>(`/platform/tenants/${tenantId}/feature-flags`);
  setTenantFlags = (tenantId: Uuid, overrides: Record<string, boolean | null>) =>
    this.api.put<TenantFlagState[]>(`/platform/tenants/${tenantId}/feature-flags`, { overrides });

  // signup requests
  signups = (q: PageQuery) => this.api.get<Page<SignupRequest>>('/platform/signup-requests', q);
  approveSignup = (id: Uuid, body: { note?: string | null; slug?: string | null; with_trial: boolean }) =>
    this.api.post<TenantProvisioned>(`/platform/signup-requests/${id}/approve`, body);
  rejectSignup = (id: Uuid, note: string | null) => this.api.post<SignupRequest>(`/platform/signup-requests/${id}/reject`, { note });

  // plans & subscriptions
  plans = () => this.api.get<Plan[]>('/platform/plans');
  createPlan = (body: Partial<Plan>) => this.api.post<Plan>('/platform/plans', body);
  updatePlan = (id: Uuid, body: Partial<Plan>) => this.api.patch<Plan>(`/platform/plans/${id}`, body);
  subscriptions = (q: PageQuery) => this.api.get<Page<Subscription>>('/platform/subscriptions', q);
  startSubscription = (tenantId: Uuid, body: { plan_id: Uuid; billing_cycle: BillingCycle; with_trial: boolean }) =>
    this.api.post<Subscription>(`/platform/tenants/${tenantId}/subscription`, body);
  changePlan = (id: Uuid, body: { plan_id: Uuid; billing_cycle: BillingCycle }) =>
    this.api.post<{ subscription: Subscription; invoice: Invoice | null; mode: string }>(`/platform/subscriptions/${id}/change-plan`, body);
  cancelSubscription = (id: Uuid, immediately: boolean) => this.api.post<Subscription>(`/platform/subscriptions/${id}/cancel`, { immediately });
  resumeSubscription = (id: Uuid) => this.api.post<Subscription>(`/platform/subscriptions/${id}/resume`);

  // invoices
  invoices = (q: PageQuery) => this.api.get<Page<Invoice>>('/platform/invoices', q);
  markPaid = (id: Uuid, payment_reference: string) => this.api.post<Invoice>(`/platform/invoices/${id}/mark-paid`, { payment_reference });
  voidInvoice = (id: Uuid, reason: string) => this.api.post<Invoice>(`/platform/invoices/${id}/void`, { reason });

  // licensing
  licenses = (q: PageQuery) => this.api.get<Page<License>>('/platform/licenses', q);
  issueLicense = (body: Record<string, unknown>) => this.api.post<{ license: License; license_key: string }>('/platform/licenses', body);
  revokeLicense = (id: Uuid, reason: string) => this.api.post<License>(`/platform/licenses/${id}/revoke`, { reason });
  updateLicense = (id: Uuid, body: Record<string, unknown>) => this.api.patch<License>(`/platform/licenses/${id}`, body);
  deployments = (q: PageQuery) => this.api.get<Page<Deployment>>('/platform/deployments', q);
  createDeployment = (body: Record<string, unknown>) => this.api.post<Deployment>('/platform/deployments', body);
  updateDeployment = (id: Uuid, body: Record<string, unknown>) => this.api.patch<Deployment>(`/platform/deployments/${id}`, body);

  // audit
  audit = (q: PageQuery) => this.api.get<Page<AuditEntry>>('/platform/audit-logs', q);

  changePassword = (current_password: string, new_password: string) =>
    this.api.post<Message>('/platform/auth/me/password', { current_password, new_password });
}

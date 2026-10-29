// DTOs mirroring the API schemas (see /api/openapi.json).

export type Uuid = string;
export type IsoDateTime = string;

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface PageQuery {
  page?: number;
  page_size?: number;
  q?: string | null;
  sort?: string | null;
  [key: string]: string | number | boolean | null | undefined | string[];
}

export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown>; request_id: string | null };
}

export interface Message {
  message: string;
}

// ---------------------------------------------------------------- auth

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
}

export type AccountType = 'admin' | 'operator' | 'declarator' | 'master_agent' | 'agent' | 'player';
export type AccountStatus = 'active' | 'suspended';

export interface TenantProfile {
  id: Uuid;
  username: string;
  email: string | null;
  full_name: string;
  phone: string | null;
  account_type: AccountType;
  parent_id: Uuid | null;
  must_change_password: boolean;
  last_login_at: IsoDateTime | null;
  created_at: IsoDateTime;
  permissions: string[];
  roles: string[];
  tenant: { id: Uuid; slug: string; name: string };
}

export type PlatformRole = 'super_admin' | 'admin' | 'billing' | 'support';

export interface PlatformProfile {
  id: Uuid;
  email: string;
  full_name: string;
  role: PlatformRole;
  last_login_at: IsoDateTime | null;
}

export interface LoginResponse<U> extends TokenResponse {
  user: U;
}

// ---------------------------------------------------------------- tenants & branding

export type TenantStatus = 'pending' | 'active' | 'suspended' | 'cancelled';
export type ModuleKey = 'events' | 'matches' | 'points' | 'reports' | 'notifications';

export interface Branding {
  app_name: string;
  logo_url: string | null;
  favicon_url: string | null;
  primary_color: string;
  secondary_color: string;
  background_color: string;
  surface_color: string;
  login_title: string | null;
  login_subtitle: string | null;
  login_background_url: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  contact_address: string | null;
  footer_text: string | null;
}

export interface PublicTenantConfig {
  id: Uuid;
  slug: string;
  name: string;
  status: TenantStatus;
  branding: Branding;
  modules: ModuleKey[];
  feature_flags: Record<string, boolean>;
  allow_self_registration: boolean;
  timezone: string;
  locale: string;
}

export interface TenantSettings {
  timezone: string;
  locale: string;
  allow_self_registration: boolean;
  enabled_modules: ModuleKey[];
  suspension_grace_days: number;
}

export interface Domain {
  id: Uuid;
  domain: string;
  kind: 'subdomain' | 'custom';
  is_primary: boolean;
  verification_status: 'pending' | 'verified' | 'failed';
  verified_at: IsoDateTime | null;
  last_checked_at: IsoDateTime | null;
  instructions: { txt_name: string; txt_value: string; cname_name: string; cname_value: string } | null;
}

export interface Tenant {
  id: Uuid;
  slug: string;
  name: string;
  status: TenantStatus;
  deployment_mode: 'shared' | 'dedicated';
  suspended_reason: string | null;
  suspended_at: IsoDateTime | null;
  created_at: IsoDateTime;
  primary_domain: string | null;
  plan_code: string | null;
  plan_name: string | null;
  subscription_status: SubscriptionStatus | null;
  user_count: number | null;
}

export interface TenantDetail extends Tenant {
  branding: Branding | null;
  settings: TenantSettings | null;
  domains: Domain[];
}

export interface TenantProvisioned {
  tenant: Tenant;
  admin_username: string;
  temporary_password: string | null;
  login_url: string;
}

export interface FeatureFlag {
  id: Uuid;
  key: string;
  description: string | null;
  default_enabled: boolean;
}

export interface TenantFlagState {
  key: string;
  description: string | null;
  default_enabled: boolean;
  override: boolean | null;
  effective: boolean;
}

export interface UsagePoint {
  metric: string;
  period_date: string;
  value: number;
}

export interface SignupRequest {
  id: Uuid;
  company_name: string;
  contact_name: string;
  contact_email: string;
  contact_phone: string | null;
  desired_slug: string;
  plan_id: Uuid;
  billing_cycle: BillingCycle;
  message: string | null;
  status: 'pending' | 'approved' | 'rejected';
  reviewed_at: IsoDateTime | null;
  review_note: string | null;
  tenant_id: Uuid | null;
  created_at: IsoDateTime;
}

// ---------------------------------------------------------------- billing

export type BillingCycle = 'monthly' | 'annual';
export type SubscriptionStatus = 'trialing' | 'active' | 'past_due' | 'expired' | 'cancelled';
export type InvoiceStatus = 'draft' | 'open' | 'paid' | 'void';

export interface Plan {
  id: Uuid;
  code: string;
  name: string;
  description: string | null;
  kind: 'saas' | 'maintenance';
  price_monthly_cents: number;
  price_annual_cents: number;
  currency: string;
  trial_days: number;
  limits: Record<string, number | null>;
  modules: ModuleKey[];
  is_active: boolean;
  sort_order: number;
}

export interface Subscription {
  id: Uuid;
  tenant_id: Uuid;
  tenant_name: string | null;
  tenant_slug: string | null;
  plan: Plan;
  pending_plan: Plan | null;
  billing_cycle: BillingCycle;
  pending_billing_cycle: BillingCycle | null;
  status: SubscriptionStatus;
  trial_ends_at: IsoDateTime | null;
  current_period_start: IsoDateTime;
  current_period_end: IsoDateTime;
  cancel_at_period_end: boolean;
  cancelled_at: IsoDateTime | null;
  expired_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

export interface InvoiceLine {
  id: Uuid;
  kind: 'plan' | 'proration' | 'credit';
  description: string;
  quantity: number;
  unit_amount_cents: number;
  amount_cents: number;
}

export interface Invoice {
  id: Uuid;
  tenant_id: Uuid;
  tenant_name: string | null;
  subscription_id: Uuid | null;
  number: string;
  status: InvoiceStatus;
  currency: string;
  subtotal_cents: number;
  tax_cents: number;
  total_cents: number;
  period_start: IsoDateTime | null;
  period_end: IsoDateTime | null;
  issued_at: IsoDateTime | null;
  due_at: IsoDateTime | null;
  paid_at: IsoDateTime | null;
  payment_reference: string | null;
  notes: string | null;
  lines: InvoiceLine[];
}

export interface PlatformOverview {
  tenants_by_status: Record<TenantStatus, number>;
  subscriptions_by_status: Partial<Record<SubscriptionStatus, number>>;
  mrr_cents: Record<string, number>;
  trials_ending_7d: number;
  open_invoices: number;
  overdue_invoices: number;
  revenue_30d_cents: Record<string, number>;
  total_users: number;
  new_tenants_30d: number;
  dedicated_deployments: number;
  usage_30d: Record<string, number>;
}

// ---------------------------------------------------------------- licensing

export type LicenseStatus = 'issued' | 'active' | 'expired' | 'revoked';

export interface License {
  id: Uuid;
  tenant_id: Uuid;
  tenant_name: string | null;
  key_prefix: string;
  license_type: 'perpetual' | 'term';
  status: LicenseStatus;
  licensee_name: string;
  max_activations: number;
  seats: number | null;
  expires_at: IsoDateTime | null;
  maintenance_until: IsoDateTime | null;
  agreement_reference: string | null;
  notes: string | null;
  revoked_at: IsoDateTime | null;
  revoked_reason: string | null;
  created_at: IsoDateTime;
  activation_count: number;
}

export interface Deployment {
  id: Uuid;
  tenant_id: Uuid;
  tenant_name: string | null;
  name: string;
  hostname: string;
  region: string | null;
  status: 'provisioning' | 'active' | 'suspended' | 'decommissioned';
  license_id: Uuid | null;
  app_version: string | null;
  last_heartbeat_at: IsoDateTime | null;
  notes: string | null;
  created_at: IsoDateTime;
}

// ---------------------------------------------------------------- users & roles

export interface RoleRef {
  id: Uuid;
  name: string;
}

export interface User {
  id: Uuid;
  username: string;
  email: string | null;
  full_name: string;
  phone: string | null;
  account_type: AccountType;
  parent_id: Uuid | null;
  parent_username: string | null;
  status: AccountStatus;
  must_change_password: boolean;
  last_login_at: IsoDateTime | null;
  created_at: IsoDateTime;
  roles: RoleRef[];
  balance: number | null;
}

export interface UserCreated {
  user: User;
  temporary_password: string | null;
}

export interface Role {
  id: Uuid;
  name: string;
  description: string | null;
  is_system: boolean;
  permissions: string[];
  user_count: number;
}

export interface Permission {
  code: string;
  description: string;
}

export interface HierarchyNode {
  id: Uuid;
  username: string;
  full_name: string;
  account_type: AccountType;
  status: AccountStatus;
  parent_id: Uuid | null;
  balance: number;
  child_count: number;
}

// ---------------------------------------------------------------- events & matches

export type EventStatus = 'draft' | 'scheduled' | 'live' | 'completed' | 'cancelled';
export type MatchStatus = 'scheduled' | 'in_progress' | 'completed' | 'cancelled';
export type MatchOutcome = 'meron' | 'wala' | 'draw' | 'no_contest';

export interface EventItem {
  id: Uuid;
  name: string;
  venue: string | null;
  description: string | null;
  starts_at: IsoDateTime;
  ends_at: IsoDateTime | null;
  status: EventStatus;
  created_at: IsoDateTime;
  match_count: number;
  completed_match_count: number;
}

export interface MatchResult {
  outcome: MatchOutcome;
  notes: string | null;
  declared_at: IsoDateTime;
  revision: number;
}

export interface Match {
  id: Uuid;
  event_id: Uuid;
  event_name: string | null;
  match_number: number;
  meron_name: string;
  meron_details: string | null;
  wala_name: string;
  wala_details: string | null;
  scheduled_at: IsoDateTime | null;
  started_at: IsoDateTime | null;
  ended_at: IsoDateTime | null;
  status: MatchStatus;
  cancel_reason: string | null;
  result: MatchResult | null;
}

export interface ResultHistoryEntry {
  id: Uuid;
  revision: number;
  outcome: MatchOutcome;
  previous_outcome: MatchOutcome | null;
  reason: string | null;
  changed_by_id: Uuid;
  changed_by_username: string | null;
  created_at: IsoDateTime;
}

// ---------------------------------------------------------------- demonstration points

export type TransactionKind = 'allocation' | 'transfer' | 'adjustment';

export interface Party {
  account_id: Uuid;
  kind: 'issuance' | 'user';
  user_id: Uuid | null;
  username: string | null;
  full_name: string | null;
}

export interface PointTransaction {
  id: Uuid;
  kind: TransactionKind;
  amount: number;
  memo: string | null;
  source: Party;
  destination: Party;
  created_by_id: Uuid;
  created_by_username: string | null;
  created_at: IsoDateTime;
}

export interface LedgerEntry {
  id: Uuid;
  transaction_id: Uuid;
  kind: TransactionKind;
  delta: number;
  balance_after: number;
  memo: string | null;
  counterparty: Party | null;
  created_at: IsoDateTime;
}

export interface PointAccount {
  user_id: Uuid;
  username: string;
  full_name: string;
  balance: number;
  disclaimer: string;
}

export interface PointsSummary {
  scope: 'tenant' | 'downline';
  total_issued: number | null;
  circulating: number;
  own_balance: number | null;
  accounts: number;
  transactions_30d: Partial<Record<TransactionKind, number>>;
  disclaimer: string;
}

// ---------------------------------------------------------------- dashboards, reports, audit

export interface DailyActivity {
  date: string;
  logins: number;
  new_accounts: number;
  point_transactions: number;
  points_moved: number;
  results_declared: number;
}

export interface AdminDashboard {
  users_by_type: Partial<Record<AccountType, number>> | null;
  active_users_7d: number | null;
  events_by_status: Record<EventStatus, number> | null;
  matches_in_progress: number | null;
  results_7d: number | null;
  points: PointsSummary | null;
  activity: DailyActivity[] | null;
  recent_audit: { id: Uuid; action: string; actor: string | null; resource_type: string; created_at: IsoDateTime }[] | null;
  modules: ModuleKey[];
}

export interface Report<R> {
  timezone: string;
  from: string;
  to: string;
  scope?: 'tenant' | 'downline';
  rows: R[];
  totals: Record<string, number>;
}

export interface AuditEntry {
  id: Uuid;
  tenant_id: Uuid | null;
  tenant_name: string | null;
  actor_type: 'platform_user' | 'tenant_user' | 'system';
  actor_id: Uuid | null;
  actor_label: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  changes: Record<string, unknown>;
  ip: string | null;
  user_agent: string | null;
  request_id: string | null;
  created_at: IsoDateTime;
}

// ---------------------------------------------------------------- portal

export interface PortalDashboard {
  balance: number | null;
  points_disclaimer: string;
  unread_notifications: number;
  live_matches: Match[];
  upcoming_events: EventItem[];
  latest_results: Match[];
}

export interface AppNotification {
  id: Uuid;
  type: string;
  title: string;
  body: string | null;
  data: Record<string, unknown>;
  read_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

export interface ActivityItem {
  id: Uuid;
  action: string;
  resource_type: string;
  ip: string | null;
  created_at: IsoDateTime;
}

export interface RealtimeMessage<T = Record<string, unknown>> {
  type: string;
  data: T;
}

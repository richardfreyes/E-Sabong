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
  /** Null until a platform admin approves the current prices; checkout needs approval. */
  prices_approved_at?: IsoDateTime | null;
  /** Why the plan cannot be bought now: 'legacy_currency' | 'prices_unapproved' | 'inactive'. */
  review_reasons?: string[];
  checkout_ready?: boolean;
  /** Request only: approve the prices being saved. */
  approve_prices?: boolean;
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
  child_count: number;
}

// ---------------------------------------------------------------- events & matches

export type EventStatus = 'draft' | 'scheduled' | 'live' | 'completed' | 'cancelled';
export type MatchStatus = 'scheduled' | 'in_progress' | 'completed' | 'cancelled';
export type MatchOutcome = 'meron' | 'wala' | 'draw' | 'no_contest';
export type PredictionSide = 'meron' | 'wala' | 'draw';
/** Display only: a prediction's outcome never moves account points. */
export type PredictionStatus = 'pending' | 'win' | 'lose' | 'void';

/** A player's non-monetary prediction on one fight, stored server-side. */
export interface Prediction {
  id: Uuid;
  match_id: Uuid;
  event_id: Uuid;
  event_name: string;
  match_number: number;
  meron_name: string;
  wala_name: string;
  match_status: MatchStatus;
  official_outcome: MatchOutcome | null;
  side: PredictionSide;
  status: PredictionStatus;
  /** The side can still be changed. */
  open: boolean;
  changes: number;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
}

/**
 * Simulated betting with a play balance. Amounts travel as decimal strings ("1234.50") so no
 * precision is lost; the server computes every balance, pool, multiplier and payout.
 */
export type DecimalString = string;
export type BetSide = 'meron' | 'wala' | 'draw';
export type BetStatus = 'accepted' | 'won' | 'lost' | 'refunded';
export type BookState = 'open' | 'closed' | 'settled';

/** All-player totals for one fight. Never carries another player's identity or bets. */
export interface FightBook {
  match_id: Uuid;
  /** null: betting was never opened for this fight. */
  state: BookState | null;
  accepting: boolean;
  pools: Record<BetSide, DecimalString>;
  /** Unrounded multipliers (null while a side has no stake) and the 2 dp values to display. */
  odds: { meron: DecimalString | null; wala: DecimalString | null; meron_display: DecimalString | null; wala_display: DecimalString | null };
  /** Multipliers are locked (betting closed) rather than provisional. */
  final: boolean;
  draw_multiplier: DecimalString;
  commission_rate: DecimalString;
  bet_count: number;
  outcome: string | null;
  /** Increases with every change, so late or repeated realtime frames can be ignored. */
  version: number;
}

export interface SidePosition {
  stake: DecimalString;
  /** Projected gross return (stake included); null while the odds are unavailable. */
  pays: DecimalString | null;
  result: BetStatus | null;
}

export interface BetPosition {
  meron: SidePosition;
  wala: SidePosition;
  draw: SidePosition;
  settled_payout: DecimalString | null;
  settled_net: DecimalString | null;
}

export interface PlayWallet {
  balance: DecimalString;
  version: number;
  disclaimer: string;
}

export interface BetLimits {
  min_bet: DecimalString;
  max_bet: DecimalString;
  max_fight_stake: DecimalString;
  remaining_fight_stake: DecimalString;
}

export interface PlayerBook {
  book: FightBook;
  mine: BetPosition;
  wallet: PlayWallet;
  limits: BetLimits;
}

export interface Bet {
  id: Uuid;
  match_id: Uuid;
  side: BetSide;
  amount: DecimalString;
  pricing: 'pool' | 'fixed';
  multiplier: DecimalString | null;
  status: BetStatus;
  payout: DecimalString;
  net_profit: DecimalString | null;
  created_at: IsoDateTime;
  settled_at: IsoDateTime | null;
}

export interface BetPlaced extends PlayerBook {
  bet: Bet;
  replayed: boolean;
}

export interface BettingConfig {
  enabled: boolean;
  draw_multiplier: DecimalString;
  commission_rate: DecimalString;
  min_bet: DecimalString;
  max_bet: DecimalString;
  max_fight_stake: DecimalString;
  wallet: PlayWallet | null;
  disclaimer: string;
}

export interface BettingSettings {
  enabled: boolean;
  commission_rate: DecimalString;
  draw_multiplier: DecimalString;
  min_bet: DecimalString;
  max_bet: DecimalString;
  max_fight_stake: DecimalString;
  starting_balance: DecimalString;
}

export interface AdminFightBook extends FightBook {
  event_id: Uuid;
  match_number: number;
  commission_amount: DecimalString | null;
  final_meron_odds: DecimalString | null;
  final_wala_odds: DecimalString | null;
  opened_at: IsoDateTime | null;
  closed_at: IsoDateTime | null;
  settled_at: IsoDateTime | null;
}

/** What each result would pay back to players, stakes included. */
export interface Obligations { meron: DecimalString; wala: DecimalString; draw: DecimalString; cancelled: DecimalString }

export interface SettlementSummary {
  outcome: string;
  revision: number | null;
  stakes: DecimalString;
  paid_to_winners: DecimalString;
  refunds: DecimalString;
  commission: DecimalString;
  uncollected: DecimalString;
  settled_at: IsoDateTime | null;
}

/** A fight's book for staff: aggregate pools and odds, obligations while open, the settlement after. */
export interface FightBookSummary extends FightBook {
  event_id: Uuid;
  event_name: string;
  match_number: number;
  match_status: MatchStatus;
  stakes_total: DecimalString;
  obligations: Obligations | null;
  settlement: SettlementSummary | null;
  opened_at: IsoDateTime | null;
  closed_at: IsoDateTime | null;
}

export interface PlayWalletRow {
  user_id: Uuid;
  username: string;
  full_name: string | null;
  balance: DecimalString;
  granted: DecimalString;
  staked: DecimalString;
  returned: DecimalString;
  open_stakes: DecimalString;
  entries: number;
  updated_at: IsoDateTime;
}

export type PlayEntryKind = 'grant' | 'stake' | 'payout' | 'refund' | 'resettlement';

export interface PlayWalletEntry {
  id: Uuid;
  created_at: IsoDateTime;
  user_id: Uuid;
  username: string;
  kind: PlayEntryKind;
  delta: DecimalString;
  balance_after: DecimalString;
  reference: string;
  memo: string | null;
  match_id: Uuid | null;
  match_number: number | null;
  event_name: string | null;
  /** The bet behind a stake / win / refund / correction row (absent on grants). */
  bet_id?: Uuid | null;
  bet_side?: BetSide | null;
  bet_status?: 'accepted' | 'won' | 'lost' | 'refunded' | null;
  bet_amount?: DecimalString | null;
  bet_credited?: DecimalString | null;
}

/** One account's play-balance statement totals (simulated, no cash value). */
export interface PlayStatement {
  user_id: Uuid;
  username: string;
  balance: DecimalString;
  granted: DecimalString;
  staked: DecimalString;
  winnings_credited: DecimalString;
  refunds: DecimalString;
  corrections: DecimalString;
  net_result: DecimalString;
  bets: number;
  bets_won: number;
  bets_lost: number;
  bets_refunded: number;
  bets_open: number;
  lost_stakes: DecimalString;
  open_stakes: DecimalString;
  entries: number;
}

export interface PlayReconciliation {
  granted: DecimalString;
  grants: number;
  staked: DecimalString;
  paid_to_winners: DecimalString;
  refunded: DecimalString;
  resettled: DecimalString;
  in_wallets: DecimalString;
  wallets: number;
  at_stake: DecimalString;
  house_result: DecimalString;
  commission: DecimalString;
  checks: { wallets_match_ledger: boolean; wallet_mismatches: number; pool_mismatches: number; uncollected_bets: number; uncollected_amount: DecimalString };
  balanced: boolean;
  disclaimer: string;
}

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
  /** A Declarator is assigned: the Declarator's results are final; admins do not declare or approve. */
  declarator_assigned?: boolean;
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

// ---------------------------------------------------------------- points wallets

export type TransactionKind = 'allocation' | 'transfer' | 'adjustment' | 'refund';
/** Display type: an allocation is an `issue` into the issuing Admin's own wallet or a `credit` into another account. */
export type TransactionType = 'issue' | 'credit' | 'transfer' | 'refund' | 'adjustment';
/** Ledger rows exist only after an atomic commit; `reversed` transfers have a posted refund. */
export type TransactionStatus = 'completed' | 'reversed';

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
  type: TransactionType;
  /** Adjustments only. */
  direction: 'credit' | 'debit' | null;
  status: TransactionStatus;
  amount: number;
  memo: string | null;
  source: Party;
  destination: Party;
  created_by_id: Uuid;
  created_by_username: string | null;
  created_at: IsoDateTime;
  tenant_id: Uuid;
  reference: string;
  source_balance_before: number;
  source_balance_after: number;
  destination_balance_before: number;
  destination_balance_after: number;
  reverses_transaction_id: Uuid | null;
  refund_transaction_id: Uuid | null;
}

export interface LedgerEntry {
  id: Uuid;
  transaction_id: Uuid;
  kind: TransactionKind;
  entry_type: 'credit' | 'debit';
  delta: number;
  balance_before: number;
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
  account_id?: Uuid | null;
  disclaimer: string;
}

export interface PointsSummary {
  scope: 'tenant' | 'downline';
  total_issued: number | null;
  circulating: number;
  own_balance: number | null;
  accounts: number;
  transactions_30d: Partial<Record<TransactionKind, number>>;
  /** Every issuance in scope. */
  gross_issued: number;
  /** Transfers and refunds out of your wallet. */
  total_transferred: number;
  /** Transfers and refunds into your wallet. */
  total_received: number;
  /** Points you placed in other accounts: outgoing transfers and direct credits. */
  total_distributed: number;
  completed_transactions: number;
  disclaimer: string;
}

export interface PointsHistoryQuery extends PageQuery {
  type?: TransactionType | null;
  status?: TransactionStatus | null;
  sender_id?: Uuid | null;
  recipient_id?: Uuid | null;
  date_from?: string | null;
  date_to?: string | null;
  user_id?: Uuid | null;
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

// ---- Live streaming (MediaMTX) ----
export type StreamSourceKind = 'obs' | 'external' | 'test';
export type StreamState = 'offline' | 'connecting' | 'live' | 'reconnecting' | 'error';

export interface StreamIngest {
  rtmp_server: string | null;
  rtmps_server: string | null;
  stream_key_format: string;
  /** Same-origin path; prefix with the portal origin for OBS's WHIP output. */
  whip_path: string;
}

export interface LiveStream {
  id: Uuid;
  name: string;
  source_kind: StreamSourceKind;
  source_protocol: string | null;
  source_host: string | null;
  event_id: Uuid | null;
  event_name: string | null;
  is_default: boolean;
  enabled: boolean;
  status: StreamState;
  status_detail: string | null;
  status_changed_at: string | null;
  last_live_at: string | null;
  checked_at: string | null;
  viewers: number;
  tracks: string[] | null;
  webrtc_ready: boolean;
  publish_key_hint: string | null;
  key_rotated_at: string | null;
  created_at: string;
  updated_at: string;
  ingest: StreamIngest | null;
}

/** Only returned right after creation or key rotation. */
export interface LiveStreamWithKey extends LiveStream {
  publish_key: string | null;
  obs_stream_key: string | null;
}

export interface StreamHealth {
  configured: boolean;
  reachable: boolean;
  detail: string | null;
  streams: number;
  live: number;
  viewers: number;
  webrtc_sessions: number;
  hls_sessions: number;
  publishers: number;
  outbound_kbps: number | null;
  test_source_available: boolean;
  rtmp_server: string | null;
  rtmps_server: string | null;
  checked_at: string;
}

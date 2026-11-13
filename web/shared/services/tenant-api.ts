import { Injectable, inject } from '@angular/core';


import type { PlaybackSource, StreamBrief } from '../arena/video-stage';
import type {
  AccountType,
  LiveStream,
  LiveStreamWithKey,
  StreamHealth,
  AdminFightBook,
  FightBookSummary,
  ActivityItem,
  AdminDashboard,
  AppNotification,
  AuditEntry,
  BalanceAccount,
  BalanceAccountDetail,
  BalanceCorrectionRequest,
  BalanceOperation,
  BalanceResetRequest,
  BalanceSummary,
  BetPlaced,
  BetSide,
  BettingConfig,
  BettingSettings,
  Branding,
  DailyActivity,
  Domain,
  EventItem,
  HierarchyNode,
  Invoice,
  LedgerEntry,
  Match,
  MatchOutcome,
  Message,
  Page,
  PageQuery,
  PlayReconciliation,
  PlayStatement,
  PlayWalletEntry,
  PlayWalletRow,
  PlayerBook,
  Permission,
  Prediction,
  PredictionSide,
  PointAccount,
  PointTransaction,
  PointsHistoryQuery,
  PointsSummary,
  PortalDashboard,
  Report,
  AccountGrantRequest,
  EarningsToWalletRequest,
  EarningsToWalletResult,
  UplineReturnRow,
  WalletRecallRequest,
  WalletRecallResult,
  WalletTransferRequest,
  WalletTransferResult,
  LoaderAccount,
  LoaderAllocation,
  LoaderGrant,
  UniformGrantRequest,
  ResultHistoryEntry,
  Role,
  Subscription,
  TenantProfile,
  TenantSettings,
  User,
  UserCreated,
  Uuid,
} from '@shared/models';
import { ApiClient, newIdempotencyKey } from './api-client';

/** Tenant administration endpoints (admin portal). */
/** Roles an account of this type may hold. The API rejects the Declarator role on any other account type. */
export function assignableRoles(roles: Role[], accountType: AccountType | null | undefined): Role[] {
  return roles.filter((r) => {
    if (r.is_system && r.name === 'declarator') return accountType === 'declarator';
    if (r.is_system && r.name === 'loader') return accountType === 'loader';
    return true;
  });
}

@Injectable({ providedIn: 'root' })
export class AdminApi {
  private readonly api = inject(ApiClient);

  dashboard = () => this.api.get<AdminDashboard>('/admin/dashboard');

  // users & hierarchy
  users = (q: PageQuery) => this.api.get<Page<User>>('/admin/users', q);
  user = (id: Uuid) => this.api.get<User>(`/admin/users/${id}`);
  createUser = (body: Record<string, unknown>) => this.api.post<UserCreated>('/admin/users', body);
  updateUser = (id: Uuid, body: Record<string, unknown>) => this.api.patch<User>(`/admin/users/${id}`, body);
  deleteUser = (id: Uuid) => this.api.delete<Message>(`/admin/users/${id}`);
  suspendUser = (id: Uuid) => this.api.post<User>(`/admin/users/${id}/suspend`);
  activateUser = (id: Uuid) => this.api.post<User>(`/admin/users/${id}/activate`);
  resetPassword = (id: Uuid) => this.api.post<{ temporary_password: string }>(`/admin/users/${id}/reset-password`);
  hierarchy = (parentId?: Uuid | null) => this.api.get<HierarchyNode[]>('/admin/hierarchy', { parent_id: parentId ?? null });

  // roles
  roles = () => this.api.get<Role[]>('/admin/roles');
  permissions = () => this.api.get<Permission[]>('/admin/permissions');
  createRole = (body: { name: string; description: string | null; permissions: string[] }) => this.api.post<Role>('/admin/roles', body);
  updateRole = (id: Uuid, body: { description?: string | null; permissions?: string[] }) => this.api.patch<Role>(`/admin/roles/${id}`, body);
  deleteRole = (id: Uuid) => this.api.delete<Message>(`/admin/roles/${id}`);

  // events & matches
  events = (q: PageQuery) => this.api.get<Page<EventItem>>('/admin/events', q);
  // Live streaming (MediaMTX)
  streams = () => this.api.get<LiveStream[]>('/admin/streams');
  streamHealth = () => this.api.get<StreamHealth>('/admin/streams/health');
  createStream = (body: Record<string, unknown>) => this.api.post<LiveStreamWithKey>('/admin/streams', body);
  updateStream = (id: Uuid, body: Record<string, unknown>) => this.api.patch<LiveStream>(`/admin/streams/${id}`, body);
  deleteStream = (id: Uuid) => this.api.delete<void>(`/admin/streams/${id}`);
  rotateStreamKey = (id: Uuid) => this.api.post<LiveStreamWithKey>(`/admin/streams/${id}/rotate-key`);
  reconnectStream = (id: Uuid) => this.api.post<LiveStream>(`/admin/streams/${id}/reconnect`);
  previewStream = (id: Uuid) =>
    this.api.post<{ status: string; message: string; playback: PlaybackSource | null; stream: StreamBrief | null }>(
      `/admin/streams/${id}/preview`,
    );
  event = (id: Uuid) => this.api.get<EventItem>(`/admin/events/${id}`);
  createEvent = (body: Record<string, unknown>) => this.api.post<EventItem>('/admin/events', body);
  updateEvent = (id: Uuid, body: Record<string, unknown>) => this.api.patch<EventItem>(`/admin/events/${id}`, body);
  deleteEvent = (id: Uuid) => this.api.delete<Message>(`/admin/events/${id}`);
  eventAction = (id: Uuid, action: 'publish' | 'unpublish' | 'start' | 'complete' | 'cancel') =>
    this.api.post<EventItem>(`/admin/events/${id}/${action}`);
  matches = (eventId: Uuid) => this.api.get<Match[]>(`/admin/events/${eventId}/matches`);
  createMatch = (eventId: Uuid, body: Record<string, unknown>) => this.api.post<Match>(`/admin/events/${eventId}/matches`, body);
  updateMatch = (id: Uuid, body: Record<string, unknown>) => this.api.patch<Match>(`/admin/matches/${id}`, body);
  deleteMatch = (id: Uuid) => this.api.delete<Message>(`/admin/matches/${id}`);
  startMatch = (id: Uuid) => this.api.post<Match>(`/admin/matches/${id}/start`);
  cancelMatch = (id: Uuid, reason: string) => this.api.post<Match>(`/admin/matches/${id}/cancel`, { reason });
  // simulated betting
  bettingSettings = () => this.api.get<BettingSettings>('/admin/betting/settings');
  updateBettingSettings = (body: Partial<BettingSettings>) => this.api.put<BettingSettings>('/admin/betting/settings', body);
  eventBooks = (eventId: Uuid) => this.api.get<AdminFightBook[]>(`/admin/events/${eventId}/books`);
  openBetting = (matchId: Uuid) => this.api.post<AdminFightBook>(`/admin/matches/${matchId}/betting/open`);
  closeBetting = (matchId: Uuid) => this.api.post<AdminFightBook>(`/admin/matches/${matchId}/betting/close`);
  bettingBooks = (q: PageQuery) => this.api.get<Page<FightBookSummary>>('/admin/betting/books', q);
  playWallets = (q: PageQuery) => this.api.get<Page<PlayWalletRow>>('/admin/betting/wallets', q);
  playEntries = (q: PageQuery) => this.api.get<Page<PlayWalletEntry>>('/admin/betting/entries', q);
  playReconciliation = () => this.api.get<PlayReconciliation>('/admin/betting/reconciliation');
  /** One account's statement totals (ledger viewers only). Optional `date_from` / `date_to` (UTC days). */
  playStatement = (userId: Uuid, q: Record<string, unknown> = {}) => this.api.get<PlayStatement>(`/admin/betting/statements/${userId}`, q);

  // balance management: views are scoped by the API (admins: tenant, master agents / agents: downline).
  // Writes add balance to one player, or apply a uniform operation. The client never sends a sender, tenant or balance.
  balanceSummary = () => this.api.get<BalanceSummary>('/admin/balance/summary');
  balanceAccounts = (q: PageQuery) => this.api.get<Page<BalanceAccount>>('/admin/balance/accounts', q);
  balanceAccount = (id: Uuid, q: Record<string, unknown> = {}) => this.api.get<BalanceAccountDetail>(`/admin/balance/accounts/${id}`, q);
  balanceEntries = (q: PageQuery) => this.api.get<Page<PlayWalletEntry>>('/admin/balance/entries', q);
  balanceEntry = (id: Uuid) => this.api.get<PlayWalletEntry>(`/admin/balance/entries/${id}`);
  balanceOperations = (q: PageQuery) => this.api.get<Page<BalanceOperation>>('/admin/balance/operations', q);
  balanceOperation = (id: Uuid) => this.api.get<BalanceOperation>(`/admin/balance/operations/${id}`);
  accountGrant = (body: AccountGrantRequest) => this.api.post<BalanceOperation>('/admin/balance/account-grants', body);
  walletTransfer = (body: WalletTransferRequest) => this.api.post<WalletTransferResult>('/admin/balance/admin-debits', body);
  walletRecall = (body: WalletRecallRequest) => this.api.post<WalletRecallResult>('/admin/balance/upline-returns', body);
  uplineReturns = (q: PageQuery) => this.api.get<Page<UplineReturnRow>>('/admin/balance/upline-returns', q);
  earningsToWallet = (body: EarningsToWalletRequest) => this.api.post<EarningsToWalletResult>('/admin/balance/commission-credits', body);
  loaderAllocation = () => this.api.get<LoaderAllocation>('/loader/allocation');
  loaderAccounts = (q: PageQuery) => this.api.get<Page<LoaderAccount>>('/loader/accounts', q);
  loaderHistory = (q: PageQuery) => this.api.get<Page<LoaderGrant>>('/loader/history', q);
  loaderGrant = (body: { target_user_id: Uuid; amount: string; reason: string; idempotency_key: string }) =>
    this.api.post<LoaderGrant>('/loader/grants', body);
  uniformGrant = (body: UniformGrantRequest) => this.api.post<BalanceOperation>('/admin/balance/grants', body);
  resetBalances = (body: BalanceResetRequest) => this.api.post<BalanceOperation>('/admin/balance/resets', body);
  proposeCorrection = (body: BalanceCorrectionRequest) => this.api.post<BalanceOperation>('/admin/balance/corrections', body);
  approveCorrection = (id: Uuid, remarks: string | null) =>
    this.api.post<BalanceOperation>(`/admin/balance/corrections/${id}/approve`, { remarks });
  rejectCorrection = (id: Uuid, remarks: string) => this.api.post<BalanceOperation>(`/admin/balance/corrections/${id}/reject`, { remarks });
  declareResult = (id: Uuid, outcome: MatchOutcome, notes: string | null) => this.api.post<Match>(`/admin/matches/${id}/result`, { outcome, notes });
  correctResult = (id: Uuid, outcome: MatchOutcome, reason: string) => this.api.put<Match>(`/admin/matches/${id}/result`, { outcome, reason });
  resultHistory = (id: Uuid) => this.api.get<ResultHistoryEntry[]>(`/admin/matches/${id}/result/history`);

  // Read-only legacy archive; no mutation endpoints remain.
  pointAccounts = (q: PageQuery) => this.api.get<Page<PointAccount>>('/admin/points/accounts', q);
  pointsSummary = () => this.api.get<PointsSummary>('/admin/points/summary');
  myPoints = () => this.api.get<PointAccount>('/admin/points/me');
  transactions = (q: PointsHistoryQuery) => this.api.get<Page<PointTransaction>>('/admin/points/transactions', q);
  transaction = (id: Uuid) => this.api.get<PointTransaction>(`/admin/points/transactions/${id}`);
  exportTransactions = (q: PointsHistoryQuery) => this.api.download('/admin/points/transactions/export', q);
  pointAccount = (userId: Uuid) => this.api.get<PointAccount>(`/admin/points/accounts/${userId}`);
  accountEntries = (userId: Uuid, q: PageQuery) => this.api.get<Page<LedgerEntry>>(`/admin/points/accounts/${userId}/entries`, q);
  // reports & audit
  activityReport = (q: Record<string, unknown>) => this.api.get<Report<DailyActivity>>('/admin/reports/activity', q);
  pointsReport = (q: Record<string, unknown>) => this.api.get<Report<Record<string, string | number>>>('/admin/reports/points', q);
  matchesReport = (q: Record<string, unknown>) => this.api.get<Report<Record<string, string | number>>>('/admin/reports/matches', q);
  downloadReport = (kind: 'activity' | 'points' | 'matches', q: Record<string, unknown>) =>
    this.api.download(`/admin/reports/${kind}`, { ...q, format: 'csv' });
  audit = (q: PageQuery) => this.api.get<Page<AuditEntry>>('/admin/audit-logs', q);

  // settings, branding, domains, billing
  settings = () => this.api.get<TenantSettings>('/admin/settings');
  saveSettings = (body: Partial<TenantSettings>) => this.api.put<TenantSettings>('/admin/settings', body);
  branding = () => this.api.get<Branding>('/admin/branding');
  saveBranding = (body: Partial<Branding>) => this.api.put<Branding>('/admin/branding', body);
  uploadAsset = (kind: 'logo' | 'favicon' | 'login_background', file: File) => this.api.upload<Branding>(`/admin/branding/assets/${kind}`, file);
  removeAsset = (kind: 'logo' | 'favicon' | 'login_background') => this.api.delete<Branding>(`/admin/branding/assets/${kind}`);
  domains = () => this.api.get<Domain[]>('/admin/domains');
  addDomain = (domain: string) => this.api.post<Domain>('/admin/domains', { domain });
  verifyDomain = (id: Uuid) => this.api.post<Domain>(`/admin/domains/${id}/verify`);
  removeDomain = (id: Uuid) => this.api.delete<Message>(`/admin/domains/${id}`);
  subscription = () => this.api.get<Subscription | null>('/admin/subscription');
  invoices = (q: PageQuery) => this.api.get<Page<Invoice>>('/admin/invoices', q);
}

/** End-user portal endpoints. */
@Injectable({ providedIn: 'root' })
export class PortalApi {
  private readonly api = inject(ApiClient);

  dashboard = () => this.api.get<PortalDashboard>('/portal/dashboard');
  events = (q: PageQuery) => this.api.get<Page<EventItem>>('/portal/events', q);
  event = (id: Uuid) => this.api.get<EventItem>(`/portal/events/${id}`);
  eventMatches = (id: Uuid) => this.api.get<Match[]>(`/portal/events/${id}/matches`);
  match = (id: Uuid) => this.api.get<Match>(`/portal/matches/${id}`);
  predictions = (q: PageQuery) => this.api.get<Page<Prediction>>('/portal/predictions', q);
  predict = (matchId: Uuid, side: PredictionSide) => this.api.put<Prediction>(`/portal/matches/${matchId}/prediction`, { side });
  // Play balance (simulated betting); request_id makes a resent bet a no-op
  betting = () => this.api.get<BettingConfig>('/portal/betting');
  streamBalance = () => this.api.get<NonNullable<BettingConfig['wallet']>>('/video/access');
  book = (matchId: Uuid) => this.api.get<PlayerBook>(`/portal/matches/${matchId}/book`);
  /** All-player MERON and WALA totals for an event. The higher total is L, the lower is D. */
  eventPools = (eventId: Uuid) => this.api.get<{ match_id: Uuid; meron: string; wala: string }[]>(`/portal/events/${eventId}/pools`);
  placeBet = (matchId: Uuid, side: BetSide, amount: string, requestId: string) =>
    this.api.post<BetPlaced>(`/portal/matches/${matchId}/bets`, { side, amount, request_id: requestId });
  results = (q: PageQuery) => this.api.get<Page<Match>>('/portal/results', q);
  /** The signed-in player's own play-balance ledger (always scoped to the caller by the server). */
  arenaPointsHistory = (q: PageQuery) => this.api.get<Page<PlayWalletEntry>>('/portal/betting/entries', q);
  /** The signed-in player's own statement totals: staked, wins, losses, refunds, corrections. */
  myStatement = (q: Record<string, unknown> = {}) => this.api.get<PlayStatement>('/portal/betting/statement', q);
  activity = (q: PageQuery) => this.api.get<Page<ActivityItem>>('/portal/activity', q);
  notifications = (q: PageQuery) => this.api.get<Page<AppNotification>>('/portal/notifications', q);
  readNotification = (id: Uuid) => this.api.post<AppNotification>(`/portal/notifications/${id}/read`);
  readAll = () => this.api.post<Message>('/portal/notifications/read-all');
}

/** Profile endpoints shared by the admin and player portals. */
@Injectable({ providedIn: 'root' })
export class AccountApi {
  private readonly api = inject(ApiClient);

  updateProfile = (body: { full_name?: string; email?: string | null; phone?: string | null }) => this.api.patch<TenantProfile>('/auth/me', body);
  changePassword = (current_password: string, new_password: string) => this.api.post<Message>('/auth/me/password', { current_password, new_password });
}

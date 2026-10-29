import { Injectable, inject } from '@angular/core';

import type {
  AccountType,
  ActivityItem,
  AdminDashboard,
  AppNotification,
  AuditEntry,
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
  Permission,
  Prediction,
  PredictionSide,
  PointAccount,
  PointTransaction,
  PointsSummary,
  PortalDashboard,
  Report,
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
  return roles.filter((r) => !(r.is_system && r.name === 'declarator') || accountType === 'declarator');
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
  declareResult = (id: Uuid, outcome: MatchOutcome, notes: string | null) => this.api.post<Match>(`/admin/matches/${id}/result`, { outcome, notes });
  correctResult = (id: Uuid, outcome: MatchOutcome, reason: string) => this.api.put<Match>(`/admin/matches/${id}/result`, { outcome, reason });
  resultHistory = (id: Uuid) => this.api.get<ResultHistoryEntry[]>(`/admin/matches/${id}/result/history`);

  // demonstration points (each mutation carries a fresh idempotency key)
  pointsSummary = () => this.api.get<PointsSummary>('/admin/points/summary');
  myPoints = () => this.api.get<PointAccount>('/admin/points/me');
  transactions = (q: PageQuery) => this.api.get<Page<PointTransaction>>('/admin/points/transactions', q);
  pointAccount = (userId: Uuid) => this.api.get<PointAccount>(`/admin/points/accounts/${userId}`);
  accountEntries = (userId: Uuid, q: PageQuery) => this.api.get<Page<LedgerEntry>>(`/admin/points/accounts/${userId}/entries`, q);
  allocate = (body: { user_id: Uuid; amount: number; memo: string | null }, key = newIdempotencyKey('alloc')) =>
    this.api.post<PointTransaction>('/admin/points/allocations', body, { 'Idempotency-Key': key });
  transfer = (body: { to_user_id: Uuid; amount: number; memo: string | null }, key = newIdempotencyKey('xfer')) =>
    this.api.post<PointTransaction>('/admin/points/transfers', body, { 'Idempotency-Key': key });
  adjust = (body: { user_id: Uuid; direction: 'credit' | 'debit'; amount: number; reason: string }, key = newIdempotencyKey('adj')) =>
    this.api.post<PointTransaction>('/admin/points/adjustments', body, { 'Idempotency-Key': key });

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
  results = (q: PageQuery) => this.api.get<Page<Match>>('/portal/results', q);
  points = () => this.api.get<PointAccount>('/portal/points');
  pointsHistory = (q: PageQuery) => this.api.get<Page<LedgerEntry>>('/portal/points/history', q);
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
  register = (body: Record<string, unknown>) => this.api.post<{ access_token: string; token_type: 'bearer'; expires_in: number; user: TenantProfile }>('/auth/register', body);
}

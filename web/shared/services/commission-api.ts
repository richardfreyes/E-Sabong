import { Injectable, inject } from '@angular/core';
import type { Page } from '@shared/models';
import { ApiClient } from './api-client';

/** Rates are fractions as strings: "0.0300" is 3%. Points are strings with 2 decimals. */
export interface CommissionOverview {
  role: 'master_agent' | 'agent';
  rate: string; allocation: string | null; upline_rate: string | null;
  total_earned: string; this_month: string; last_month: string;
  network_total: string; network_this_month: string; network_last_month: string;
}
export interface AgentCommissionRow {
  agent_id: string; username: string; full_name: string; status: string; players: number;
  agent_rate: string; master_rate: string; allocation: string; agent_earned: string; master_earned: string;
}
export interface CommissionHistoryRow {
  id: string; created_at: string; match_id: string; match_number: number; event_name: string;
  player_id: string; player_username: string; agent_id: string | null; agent_username: string | null;
  side: string; bet_amount: string; agent_rate: string; agent_points: string;
  master_rate: string | null; master_points: string | null;
  outcome: string; result_revision: number | null; status: 'earned' | 'reversed';
}
export interface CommissionBreakdownRow {
  key: string; label: string; bets: number; eligible_points: string; agent_points: string; master_points: string | null;
}

/** Master agent and agent commission (earnings). Read-only except a master agent setting their agents' rates. */
@Injectable({ providedIn: 'root' })
export class CommissionApi {
  private readonly api = inject(ApiClient);
  overview = () => this.api.get<CommissionOverview>('/commissions/me');
  agents = () => this.api.get<AgentCommissionRow[]>('/commissions/agents');
  setAgentRate = (agentId: string, rate: string) => this.api.put<AgentCommissionRow>(`/commissions/agents/${agentId}`, { rate });
  history = (query: { page?: number; page_size?: number; agent_id?: string | null }) =>
    this.api.get<Page<CommissionHistoryRow>>('/commissions/history', clean(query));
  breakdown = (by: 'player' | 'fight', agentId?: string | null) =>
    this.api.get<CommissionBreakdownRow[]>('/commissions/breakdown', clean({ by, agent_id: agentId }));
}

function clean(query: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(query).filter(([, v]) => v !== null && v !== undefined && v !== ''));
}

export type MyUserKind = 'player' | 'agent';
export type MyUserState = 'active' | 'blocked' | 'banned' | 'online';
export interface MyUserRow {
  id: string; code: string; username: string; full_name: string; account_type: MyUserKind; parent_username: string | null;
  direct: boolean; status: 'active' | 'suspended' | 'banned'; online: boolean; via_referral: boolean;
  balance: string | null; earnings: string; rate: string | null; created_at: string; last_login_at: string | null;
}
export interface MyUserCounts { active: number; blocked: number; banned: number; online: number }
export interface MyUsersSummary { players: MyUserCounts; agents: MyUserCounts | null }

export interface AgentWallet { balance: string; version: number }
export interface TransferPreview { player_id: string; player_username: string; player_balance: string; sender_balance: string }
export interface BalanceTransfer {
  id: string; reference: string; sender_id: string; sender_username: string; sender_role: string;
  player_id: string; player_username: string; amount: string;
  sender_balance_before: string; sender_balance_after: string; player_balance_before: string; player_balance_after: string;
  status: string; created_at: string; replayed: boolean;
}

/** The caller's downline (players and, for a master agent, agents). Scoped on the server. */
@Injectable({ providedIn: 'root' })
export class AgentUsersApi {
  private readonly api = inject(ApiClient);
  summary = () => this.api.get<MyUsersSummary>('/agent/users/summary');
  list = (query: { page: number; page_size: number; kind?: MyUserKind | null; state?: MyUserState | null; q?: string | null }) =>
    this.api.get<Page<MyUserRow>>('/agent/users', clean(query));
  /** Block / unblock: the existing downline-scoped account status endpoints. Bans are administrator-only. */
  block = (id: string) => this.api.post<unknown>(`/admin/users/${id}/suspend`, {});
  unblock = (id: string) => this.api.post<unknown>(`/admin/users/${id}/activate`, {});
  /** The caller's own transferable wallet. Earnings are not part of it. */
  wallet = () => this.api.get<AgentWallet>('/agent/wallet');
  preview = (playerId: string) => this.api.get<TransferPreview>(`/agent/users/${playerId}/transfer`);
  transfer = (playerId: string, body: { amount: string; idempotency_key: string }) =>
    this.api.post<BalanceTransfer>(`/agent/users/${playerId}/transfers`, body);
  transfers = (query: { page: number; page_size: number }) =>
    this.api.get<Page<BalanceTransfer>>('/agent/balance-transfers', query);
  inbox = () => this.api.get<InboxItem[]>('/agent/inbox');
  inboxCounts = () => this.api.get<InboxCounts>('/agent/inbox/counts');
  decide = (id: string, decision: 'approve' | 'deny') =>
    this.api.post<InboxItem>(`/agent/inbox/${id}/decision`, { decision });
}

export interface InboxItem {
  id: string; player_id: string; player_username: string; upline_id: string; upline_username: string;
  kind: 'cashout'; amount: string; status: 'pending' | 'approved' | 'denied';
  created_at: string; decided_at: string | null; replayed: boolean;
}
export interface InboxCounts { cashout: number; commission: number }

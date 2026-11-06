import { Injectable, inject } from '@angular/core';
import type { EventItem, FightBookSummary, Match, Page, PageQuery, ResultHistoryEntry } from '@shared/models';
import { ApiClient } from './api-client';

export type DeclarationOutcome = 'meron' | 'wala' | 'draw' | 'no_contest' | 'cancelled';
export type DeclarationStatus = 'pending' | 'declared' | 'pending_approval' | 'under_review' | 'finalized' | 'cancelled';
export interface Declaration {
  id: string; match_id: string; outcome: DeclarationOutcome; status: DeclarationStatus; base_revision: number;
  reason: string | null; declared_by_id: string; declared_by: string; declared_at: string;
  approved_by_id: string | null; approved_by: string | null; finalized_at: string | null;
}
export interface MatchRecord {
  match: Match; event_name: string; workflow_status: DeclarationStatus; official_winner: DeclarationOutcome | null;
  declaration: Declaration | null; declared_by: string | null; declared_at: string | null;
  approved_by: string | null; finalized_at: string | null;
}
export interface DeclarationAudit {
  id: string; declaration_id: string; actor: string; action: string; from_status: string | null;
  to_status: string; reason: string | null; created_at: string;
}
export interface DeclarationHistory {
  record: MatchRecord; declarations: Declaration[]; audit: DeclarationAudit[]; revisions: ResultHistoryEntry[];
}
/** `books`: aggregate betting per fight on the card (pools, odds, obligations, settlements). */
/**
 * The Declarator's betting switch, computed and enforced by the API. It acts on the fight in progress,
 * otherwise the next scheduled fight; `status` is 'open' only while the backend accepts simulated bets.
 */
export interface BettingControl {
  match_id: string | null; match_number: number | null; match_status: string | null;
  status: 'open' | 'closed'; book_state: 'open' | 'closed' | 'settled' | null;
  can_open: boolean; can_close: boolean; blocked_reason: string | null; next_match_number: number | null;
}
export interface DeclaratorMonitor {
  event: EventItem; matches: Match[]; current: MatchRecord | null; books: FightBookSummary[];
  /** Absent only on an API older than the betting switch. */
  betting?: BettingControl | null;
}
/** The event's automatic ghost betting: the running job, else the last one. Ghost points never touch a balance. */
/** A connected player with their play balance and stakes on the event's current fight (read-only). */
export interface OnlinePlayer {
  id: string; username: string; full_name: string; balance: string;
  meron_stake: string; wala_stake: string; draw_stake: string; last_seen_at: string;
}
export interface OnlinePlayers { players: number; fight_number?: number | null; items?: OnlinePlayer[] }

export interface GhostAuto {
  active: boolean; id?: string | null; match_id?: string | null; match_number?: number | null;
  meron_amount?: string | null; wala_amount?: string | null; interval_seconds?: number | null;
  runs: number; added_meron: string; added_wala: string; started_by?: string | null; started_at?: string | null;
  next_run_at?: string | null; stopped_at?: string | null; stop_reason?: string | null;
}
export interface GhostAutoStart { meron_amount: string; wala_amount: string; interval_seconds: number; }
/** Entries of a fight as the Declarator announces them. */
export interface DeclarationRequest { request_id: string; outcome: DeclarationOutcome; base_revision: number; reason: string | null; }
export interface EventAssignment { user_id: string; username: string; assigned_at: string; }
export const DECLARATION_LABELS: Record<DeclarationOutcome, string> = {
  meron: 'Meron wins', wala: 'Wala wins', draw: 'Draw', no_contest: 'No contest', cancelled: 'Cancelled',
};
export const WORKFLOW_LABELS: Record<DeclarationStatus, string> = {
  pending: 'Awaiting result', declared: 'Declared', pending_approval: 'Awaiting approval', under_review: 'Under review',
  finalized: 'Finalized', cancelled: 'Cancelled / returned',
};

@Injectable({ providedIn: 'root' })
export class DeclaratorApi {
  private readonly api = inject(ApiClient);
  events = () => this.api.get<EventItem[]>('/declarator/events');
  monitor = (id: string) => this.api.get<DeclaratorMonitor>(`/declarator/events/${id}/monitor`);
  records = (query: PageQuery) => this.api.get<Page<MatchRecord>>('/declarator/records', query);
  history = (id: string) => this.api.get<DeclarationHistory>(`/declarator/matches/${id}/history`);
  submit = (id: string, body: DeclarationRequest) => this.api.post<Declaration>(`/declarator/matches/${id}/declarations`, body);
  openBetting = (matchId: string) => this.api.post<BettingControl>(`/declarator/matches/${matchId}/betting/open`);
  /** Add a dummy MERON or WALA amount. It changes the pool and the odds, and no player's balance. */
  addDummyBet = (matchId: string, body: { request_id: string; side: 'meron' | 'wala'; amount: string }) =>
    this.api.post<FightBookSummary>(`/declarator/matches/${matchId}/dummy-bets`, body);
  closeBetting = (matchId: string) => this.api.post<BettingControl>(`/declarator/matches/${matchId}/betting/close`);
  /** Open betting on the event's current fight; on an empty live card the API adds the next fight first. */
  openEventBetting = (eventId: string) => this.api.post<BettingControl>(`/declarator/events/${eventId}/betting/open`);
  // ---- Declarator desk (event header, fight controls, auto ghost betting) ----
  onlinePlayers = (eventId: string) => this.api.get<OnlinePlayers>(`/declarator/events/${eventId}/online`);
  endEvent = (eventId: string) => this.api.post<EventItem>(`/declarator/events/${eventId}/end`);
  /** Every connected screen (players and staff) reloads the event. */
  refreshAll = (eventId: string) => this.api.post<{ message: string }>(`/declarator/events/${eventId}/refresh`);
  jumpToFight = (eventId: string, matchNumber: number) => this.api.post<Match>(`/declarator/events/${eventId}/jump`, { match_number: matchNumber });
  changeTeams = (matchId: string, body: { meron_name: string; wala_name: string }) => this.api.put<Match>(`/declarator/matches/${matchId}/teams`, body);
  ghostAuto = (eventId: string) => this.api.get<GhostAuto>(`/declarator/events/${eventId}/ghost-auto`);
  startGhostAuto = (eventId: string, body: GhostAutoStart) => this.api.post<GhostAuto>(`/declarator/events/${eventId}/ghost-auto/start`, body);
  stopGhostAuto = (eventId: string) => this.api.post<GhostAuto>(`/declarator/events/${eventId}/ghost-auto/stop`);
  approve = (id: string) => this.api.post<Declaration>(`/declarator/declarations/${id}/approve`);
  review = (id: string, reason: string) => this.api.post<Declaration>(`/declarator/declarations/${id}/review`, { reason });
  returnDeclaration = (id: string, reason: string) => this.api.post<Declaration>(`/declarator/declarations/${id}/return`, { reason });
  eligibleUsers = () => this.api.get<{id: string; username: string}[]>('/declarator/eligible-users');
  assignments = (event: string) => this.api.get<EventAssignment[]>(`/declarator/events/${event}/assignments`);
  assign = (event: string, user: string) => this.api.put(`/declarator/events/${event}/assignments/${user}`, {});
  unassign = (event: string, user: string) => this.api.delete(`/declarator/events/${event}/assignments/${user}`);
}

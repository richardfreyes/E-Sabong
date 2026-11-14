import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiClient } from './api-client';

export type InvitationRole = 'agent' | 'sub_agent' | 'player';
export type InvitationStatus = 'pending' | 'accepted' | 'revoked';

export interface InvitationRow {
  id: string;
  tenant_id: string;
  invited_by_user_id: string;
  invitee_user_id: string | null;
  invitee_username: string | null;
  invitee_full_name: string | null;
  account_type: InvitationRole;
  status: InvitationStatus;
  created_at: string;
}

export interface InvitationSummary {
  pending: number;
  accepted: number;
  revoked: number;
  history: number;
}

export interface InvitationPage {
  items: InvitationRow[];
  total: number;
  page: number;
  page_size: number;
}

/** Invitations created by the signed-in account. The server drops every other owner's rows. */
@Injectable({ providedIn: 'root' })
export class InvitationApi {
  private readonly api = inject(ApiClient);

  summary = () => this.api.get<InvitationSummary>('/agent/invitations/summary');

  list = (query: { page: number; page_size: number; q?: string | null; status?: InvitationStatus | null }) =>
    this.api.get<InvitationPage>('/agent/invitations', query);

  detail = (id: string) => this.api.get<InvitationRow>(`/agent/invitations/${id}`);

  create = (body: { account_type: InvitationRole; invitee_username?: string | null; invitee_full_name?: string | null }) =>
    this.api.post<InvitationRow>('/agent/invitations', body);

  revoke = (id: string) => this.api.patch<InvitationRow>(`/agent/invitations/${id}`, { status: 'revoked' });

  export = (query: { q?: string | null; status?: InvitationStatus | null }): Observable<Blob> =>
    this.api.download('/agent/invitations/export', query);
}

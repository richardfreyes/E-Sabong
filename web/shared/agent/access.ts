import { InjectionToken, inject } from '@angular/core';
import { CanActivateFn, CanMatchFn, Router } from '@angular/router';

import type { TenantProfile } from '@shared/models';
import { AuthStore } from '@shared/services';

/**
 * Which portal hosts the agent dashboard. Links to screens of the other portal become plain
 * hrefs (the admin portal lives under /admin/ on the same origin and keeps its own session).
 */
export type AgentPortal = 'admin' | 'player';
export const AGENT_PORTAL = new InjectionToken<AgentPortal>('AGENT_PORTAL', { factory: () => 'admin' });

export function isAgentAccount(type: string | null | undefined): boolean {
  return type === 'master_agent' || type === 'agent' || type === 'sub_agent';
}

function accountType(): string | undefined {
  return inject<AuthStore<TenantProfile>>(AuthStore).user()?.account_type;
}

/** Master agents and agents get their own home. Every other role keeps its existing dashboard. */
export const agentHomeMatch: CanMatchFn = () => isAgentAccount(accountType());

/** Player portal home: master agents and agents land on /agent; players keep the arena. */
export const playerHomeGuard: CanActivateFn = () =>
  isAgentAccount(accountType()) ? inject(Router).createUrlTree(['/agent']) : true;

/** The /agent workspace of the player portal is for master agents and agents only. */
export const agentOnlyGuard: CanActivateFn = () =>
  isAgentAccount(accountType()) ? true : inject(Router).createUrlTree(['/']);

/** Only Agents distribute commission to Sub-Agents. */
export const subAgentsOnlyGuard: CanActivateFn = () => accountType() === 'agent'
  ? true : inject(Router).createUrlTree(['/']);

import { Routes } from '@angular/router';

import { AGENT_PORTAL, agentOnlyGuard, playerHomeGuard } from '@shared/agent';
import { authGuard, guestGuard, moduleGuard } from '@shared/guards';
import { PlayerLayout } from './layout';

export const routes: Routes = [
  { path: 'login', canActivate: [guestGuard], loadComponent: () => import('./pages/login').then((m) => m.LoginPage), title: 'Sign in' },
  { path: 'register', canActivate: [guestGuard], loadComponent: () => import('./pages/register').then((m) => m.RegisterPage), title: 'Create account' },
  { path: 'change-password', canActivate: [authGuard], loadComponent: () => import('./pages/change-password').then((m) => m.ChangePasswordPage), title: 'Choose a password' },
  // Master agents and agents get the SWC MAIN agent workspace instead of the player arena home.
  {
    path: 'agent',
    canActivate: [authGuard, agentOnlyGuard],
    providers: [{ provide: AGENT_PORTAL, useValue: 'player' }],
    loadComponent: () => import('@shared/agent').then((m) => m.AgentFrame),
    children: [
      { path: '', pathMatch: 'full', loadComponent: () => import('@shared/agent').then((m) => m.AgentDashboardPage), title: 'Agent Dashboard' },
      { path: 'account', loadComponent: () => import('./pages/profile').then((m) => m.ProfilePage), title: 'My Account' },
      { path: 'users', loadComponent: () => import('@shared/agent').then((m) => m.AgentUsersPage), title: 'My Users' },
      { path: 'commission', loadComponent: () => import('@shared/agent').then((m) => m.AgentCommissionPage), title: 'Commission' },
      { path: 'wallet/transfer', data: { mode: 'transfer' }, loadComponent: () => import('@shared/agent').then((m) => m.AgentWalletPage), title: 'Transfer to Wallet' },
      { path: 'logs', loadComponent: () => import('@shared/agent').then((m) => m.AgentLogsPage), title: 'Logs' },
    ],
  },
  {
    path: '',
    component: PlayerLayout,
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', canActivate: [playerHomeGuard], loadComponent: () => import('./pages/dashboard').then((m) => m.DashboardPage), title: 'Arena' },
      // Live Games entry for agents, whose home is /agent. Players see the same arena at /.
      { path: 'arena', loadComponent: () => import('./pages/dashboard').then((m) => m.DashboardPage), title: 'Arena' },
      { path: 'events', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/events').then((m) => m.EventsPage), title: 'Events' },
      { path: 'events/:id', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/event-detail').then((m) => m.EventDetailPage), title: 'Event' },
      { path: 'matches/:id', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/match-detail').then((m) => m.MatchDetailPage), title: 'Match' },
      { path: 'history', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/history').then((m) => m.HistoryPage), title: 'Match history' },
      { path: 'predictions', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/predictions').then((m) => m.PredictionsPage), title: 'My predictions' },
      { path: 'results', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/results').then((m) => m.ResultsPage), title: 'Results' },
      { path: 'arena-points', loadComponent: () => import('./pages/arena-points').then((m) => m.ArenaPointsPage), title: 'Balance history' },
      // The retired points module had a player page here; old links land on the balance history instead.
      { path: 'points', redirectTo: 'arena-points' },
      { path: 'notifications', loadComponent: () => import('./pages/notifications').then((m) => m.NotificationsPage), title: 'Notifications' },
      { path: 'activity', loadComponent: () => import('./pages/activity').then((m) => m.ActivityPage), title: 'Account activity' },
      { path: 'profile', loadComponent: () => import('./pages/profile').then((m) => m.ProfilePage), title: 'Profile' },
      { path: 'settings', loadComponent: () => import('./pages/settings').then((m) => m.SettingsPage), title: 'Settings' },
    ],
  },
  { path: '**', redirectTo: '' },
];

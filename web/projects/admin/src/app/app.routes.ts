import { Routes } from '@angular/router';

import { authGuard, guestGuard, moduleGuard, permissionGuard } from '@shared/guards';
import { agentHomeMatch } from '@shared/agent';
import { declaratorLandingGuard } from './declarator/landing.guard';
import { loaderLandingGuard, loaderOnlyGuard } from './loader/landing.guard';
import { keepStreamOperatorsOut, operatorGuard } from './operator/guards';
import { AdminLayout } from './layout';

export const routes: Routes = [
  { path: 'login', canActivate: [guestGuard], loadComponent: () => import('./pages/login').then((m) => m.LoginPage), title: 'Sign in' },
  { path: 'change-password', canActivate: [authGuard], loadComponent: () => import('./pages/change-password').then((m) => m.ChangePasswordPage), title: 'Choose a password' },
  // Stream Operator dashboard: its own layout, separate from administration (also open to admins).
  {
    path: 'operator',
    canActivate: [authGuard, operatorGuard],
    loadComponent: () => import('./operator/layout').then((m) => m.OperatorLayout),
    children: [
      { path: '', loadComponent: () => import('./operator/dashboard').then((m) => m.OperatorDashboard), title: 'Operator console' },
    ],
  },
  {
    path: '',
    component: AdminLayout,
    canActivate: [authGuard, keepStreamOperatorsOut],
    canActivateChild: [permissionGuard],
    children: [
      { path: 'declarator', canMatch: [moduleGuard], data: { module: 'matches', modules: ['events', 'matches'], permissions: ['results.submit', 'results.approve'] }, loadComponent: () => import('./declarator/dashboard').then(m => m.DeclaratorDashboard), title: 'Declarator' },
      { path: '', pathMatch: 'full', canMatch: [agentHomeMatch], loadComponent: () => import('@shared/agent').then((m) => m.AgentDashboardPage), title: 'Dashboard' },
      { path: 'my-users', canMatch: [agentHomeMatch], loadComponent: () => import('@shared/agent').then((m) => m.AgentUsersPage), title: 'My Users' },
      { path: 'commission', canMatch: [agentHomeMatch], loadComponent: () => import('@shared/agent').then((m) => m.AgentCommissionPage), title: 'Commission' },
      { path: '', pathMatch: 'full', canActivate: [declaratorLandingGuard, loaderLandingGuard], loadComponent: () => import('./pages/dashboard').then((m) => m.DashboardPage), title: 'Dashboard' },
      { path: 'loader', canActivate: [loaderOnlyGuard], data: { permissions: ['loader.view'] }, loadComponent: () => import('./loader/dashboard').then((m) => m.LoaderDashboard), title: 'Loader' },
      { path: 'users', data: { permissions: ['users.view_all', 'users.view_downline', 'users.manage'] },
        loadComponent: () => import('./pages/users').then((m) => m.UsersPage), title: 'Accounts' },
      { path: 'users/:id', data: { permissions: ['users.view_all', 'users.view_downline', 'users.manage'] },
        loadComponent: () => import('./pages/user-detail').then((m) => m.UserDetailPage), title: 'Account' },
      { path: 'hierarchy', data: { permissions: ['users.view_all', 'users.view_downline', 'users.manage'] },
        loadComponent: () => import('./pages/hierarchy').then((m) => m.HierarchyPage), title: 'Hierarchy' },
      { path: 'roles', data: { permissions: ['roles.manage'] }, loadComponent: () => import('./pages/roles').then((m) => m.RolesPage), title: 'Roles' },
      { path: 'events', canMatch: [moduleGuard], data: { module: 'events', permissions: ['events.view', 'events.manage', 'matches.manage'] },
        loadComponent: () => import('./pages/events').then((m) => m.EventsPage), title: 'Events' },
      { path: 'events/:id', canMatch: [moduleGuard], data: { module: 'events', permissions: ['events.view', 'events.manage', 'matches.manage'] },
        loadComponent: () => import('./pages/event-detail').then((m) => m.EventDetailPage), title: 'Event' },
      { path: 'streams', canMatch: [moduleGuard], data: { module: 'events', permissions: ['settings.manage'] },
        loadComponent: () => import('./pages/streams').then((m) => m.StreamsPage), title: 'Live streaming' },
      { path: 'operators', canMatch: [moduleGuard], data: { module: 'events', permissions: ['settings.manage'] },
        loadComponent: () => import('./pages/operators').then((m) => m.OperatorsPage), title: 'Stream Operators' },
      { path: 'betting', canMatch: [moduleGuard], data: { module: 'matches', modules: ['events', 'matches'], permissions: ['events.view', 'events.manage', 'matches.manage', 'points.view_all'] },
        loadComponent: () => import('./pages/betting').then((m) => m.BettingPage), title: 'Betting' },
      // Read-only play-balance statement; the API enforces points.view_all and tenant scope again.
      { path: 'balance-history', canMatch: [moduleGuard], data: { module: 'matches', modules: ['events', 'matches'], permissions: ['points.view_all'] },
        loadComponent: () => import('./pages/balance-history').then((m) => m.BalanceHistoryPage), title: 'Balance History' },
      // Role-scoped balances and administrator operations; the API enforces scope, roles and tenant on every call.
      { path: 'balance-management', canMatch: [moduleGuard], data: { module: 'matches', modules: ['events', 'matches'], permissions: ['points.view_all', 'points.view_downline', 'balance.manage', 'loader.view'] },
        loadComponent: () => import('./pages/balance-management').then((m) => m.BalanceManagementPage), title: 'Balance Management' },
      { path: 'points', canMatch: [moduleGuard], data: { module: 'points', permissions: ['points.view_all', 'points.view_downline'] },
        loadComponent: () => import('./pages/points').then((m) => m.PointsPage), title: 'Legacy points archive' },
      { path: 'reports', canMatch: [moduleGuard], data: { module: 'reports', permissions: ['reports.view'] },
        loadComponent: () => import('./pages/reports').then((m) => m.ReportsPage), title: 'Reports' },
      { path: 'audit', data: { permissions: ['audit.view'] }, loadComponent: () => import('./pages/audit').then((m) => m.AuditPage), title: 'Audit log' },
      { path: 'settings', data: { permissions: ['settings.manage', 'branding.manage'] },
        loadComponent: () => import('./pages/settings').then((m) => m.SettingsPage), title: 'Settings' },
      { path: 'subscription', data: { permissions: ['billing.view'] },
        loadComponent: () => import('./pages/subscription').then((m) => m.SubscriptionPage), title: 'Subscription' },
      { path: 'profile', loadComponent: () => import('./pages/profile').then((m) => m.ProfilePage), title: 'Profile' },
      { path: 'forbidden', loadComponent: () => import('./pages/forbidden').then((m) => m.ForbiddenPage), title: 'No access' },
    ],
  },
  { path: '**', redirectTo: '' },
];

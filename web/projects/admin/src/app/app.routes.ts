import { Routes } from '@angular/router';

import { authGuard, guestGuard, moduleGuard, permissionGuard } from '@shared/guards';
import { declaratorLandingGuard } from './declarator/landing.guard';
import { AdminLayout } from './layout';

export const routes: Routes = [
  { path: 'login', canActivate: [guestGuard], loadComponent: () => import('./pages/login').then((m) => m.LoginPage), title: 'Sign in' },
  { path: 'change-password', canActivate: [authGuard], loadComponent: () => import('./pages/change-password').then((m) => m.ChangePasswordPage), title: 'Choose a password' },
  {
    path: '',
    component: AdminLayout,
    canActivate: [authGuard],
    canActivateChild: [permissionGuard],
    children: [
      { path: 'declarator', canMatch: [moduleGuard], data: { module: 'matches', modules: ['events', 'matches'], permissions: ['results.submit', 'results.approve'] }, loadComponent: () => import('./declarator/dashboard').then(m => m.DeclaratorDashboard), title: 'Declarator' },
      { path: '', pathMatch: 'full', canActivate: [declaratorLandingGuard], loadComponent: () => import('./pages/dashboard').then((m) => m.DashboardPage), title: 'Dashboard' },
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
      { path: 'points', canMatch: [moduleGuard], data: { module: 'points', permissions: ['points.view_all', 'points.view_downline'] },
        loadComponent: () => import('./pages/points').then((m) => m.PointsPage), title: 'Demonstration points' },
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

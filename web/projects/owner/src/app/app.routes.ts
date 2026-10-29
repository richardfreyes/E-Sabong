import { Routes } from '@angular/router';

import { authGuard, guestGuard } from '@shared/guards';
import { OwnerLayout } from './layout';

export const routes: Routes = [
  { path: 'login', canActivate: [guestGuard], loadComponent: () => import('./pages/login').then((m) => m.LoginPage), title: 'Sign in' },
  {
    path: '',
    component: OwnerLayout,
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', loadComponent: () => import('./pages/dashboard').then((m) => m.DashboardPage), title: 'Overview' },
      { path: 'tenants', loadComponent: () => import('./pages/tenants').then((m) => m.TenantsPage), title: 'Tenants' },
      { path: 'tenants/:id', loadComponent: () => import('./pages/tenant-detail').then((m) => m.TenantDetailPage), title: 'Tenant' },
      { path: 'signups', loadComponent: () => import('./pages/signups').then((m) => m.SignupsPage), title: 'Signup requests' },
      { path: 'plans', loadComponent: () => import('./pages/plans').then((m) => m.PlansPage), title: 'Plans' },
      { path: 'subscriptions', loadComponent: () => import('./pages/subscriptions').then((m) => m.SubscriptionsPage), title: 'Subscriptions' },
      { path: 'invoices', loadComponent: () => import('./pages/invoices').then((m) => m.InvoicesPage), title: 'Invoices' },
      { path: 'licenses', loadComponent: () => import('./pages/licenses').then((m) => m.LicensesPage), title: 'Licenses' },
      { path: 'deployments', loadComponent: () => import('./pages/deployments').then((m) => m.DeploymentsPage), title: 'Deployments' },
      { path: 'feature-flags', loadComponent: () => import('./pages/flags').then((m) => m.FlagsPage), title: 'Feature flags' },
      { path: 'audit', loadComponent: () => import('./pages/audit').then((m) => m.AuditPage), title: 'Audit log' },
      { path: 'account', loadComponent: () => import('./pages/account').then((m) => m.AccountPage), title: 'Account' },
    ],
  },
  { path: '**', redirectTo: '' },
];

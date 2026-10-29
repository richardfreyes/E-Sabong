import { Routes } from '@angular/router';

import { authGuard, guestGuard, moduleGuard } from '@shared/guards';
import { PlayerLayout } from './layout';

export const routes: Routes = [
  { path: 'login', canActivate: [guestGuard], loadComponent: () => import('./pages/login').then((m) => m.LoginPage), title: 'Sign in' },
  { path: 'register', canActivate: [guestGuard], loadComponent: () => import('./pages/register').then((m) => m.RegisterPage), title: 'Create account' },
  { path: 'change-password', canActivate: [authGuard], loadComponent: () => import('./pages/change-password').then((m) => m.ChangePasswordPage), title: 'Choose a password' },
  {
    path: '',
    component: PlayerLayout,
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', loadComponent: () => import('./pages/dashboard').then((m) => m.DashboardPage), title: 'Arena' },
      { path: 'events', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/events').then((m) => m.EventsPage), title: 'Events' },
      { path: 'events/:id', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/event-detail').then((m) => m.EventDetailPage), title: 'Event' },
      { path: 'matches/:id', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/match-detail').then((m) => m.MatchDetailPage), title: 'Match' },
      { path: 'history', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/history').then((m) => m.HistoryPage), title: 'Match history' },
      { path: 'predictions', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/predictions').then((m) => m.PredictionsPage), title: 'My predictions' },
      { path: 'results', canMatch: [moduleGuard], data: { module: 'events' }, loadComponent: () => import('./pages/results').then((m) => m.ResultsPage), title: 'Results' },
      { path: 'points', canMatch: [moduleGuard], data: { module: 'points' }, loadComponent: () => import('./pages/points').then((m) => m.PointsPage), title: 'Points history' },
      { path: 'notifications', loadComponent: () => import('./pages/notifications').then((m) => m.NotificationsPage), title: 'Notifications' },
      { path: 'activity', loadComponent: () => import('./pages/activity').then((m) => m.ActivityPage), title: 'Account activity' },
      { path: 'profile', loadComponent: () => import('./pages/profile').then((m) => m.ProfilePage), title: 'Profile' },
      { path: 'settings', loadComponent: () => import('./pages/settings').then((m) => m.SettingsPage), title: 'Settings' },
    ],
  },
  { path: '**', redirectTo: '' },
];

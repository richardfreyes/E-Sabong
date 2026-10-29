import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

/** Ensures the API is up and the demo tenant plus an owner account exist (both idempotent). */
export default async function globalSetup(): Promise<void> {
  const health = await fetch('http://localhost:8000/api/health').catch(() => null);
  if (!health?.ok) throw new Error('API is not reachable on http://localhost:8000 — start it first (see docs/testing.md).');

  if (process.env['E2E_SKIP_SEED']) return;
  const apiDir = resolve(__dirname, '../../api');
  const python = resolve(apiDir, '.venv/bin/python');
  if (!existsSync(python)) throw new Error(`Python venv not found at ${python}; set E2E_SKIP_SEED=1 if data is seeded another way.`);
  execFileSync(python, ['-m', 'app.cli', 'demo-tenant'], { cwd: apiDir, stdio: 'inherit' });
  try {
    execFileSync(python, ['-m', 'app.cli', 'create-platform-admin', '--email', OWNER.email, '--name', 'E2E Owner', '--password', OWNER.password],
      { cwd: apiDir, stdio: 'pipe' });
  } catch {
    /* already exists */
  }
}

export const OWNER = { email: 'owner@example.com', password: 'OwnerPass#2026' };

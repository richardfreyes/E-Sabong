#!/usr/bin/env node
// Fails (exit 1) when a production portal build exposes debugging or secret material.
//
//   node scripts/verify-prod-bundle.mjs dist/owner dist/admin dist/player
//
// Run by infrastructure/docker/web.Dockerfile after `ng build` (production configuration), so a
// UAT or production image can never ship with source maps or a development build. Local
// development builds (`ng serve`, `--configuration development`) are not checked and keep
// source maps, dev mode and console output.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, relative } from 'node:path';

const roots = process.argv.slice(2);
if (roots.length === 0) {
  console.error('usage: verify-prod-bundle.mjs <dist dir> [...]');
  process.exit(2);
}

// [label, pattern]: matched against .js, .css, .html, .json and .txt files of the build.
const forbidden = [
  ['source map reference', /[#@]\s*sourceMappingURL=/],
  ['Angular development mode', /Angular is running in development mode/],
  ['private key', /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  // A server secret with a value next to it (NAME=value, NAME: "value"). The bare name may appear in
  // operator help text ("set MEDIAMTX_API_PASSWORD"); a value never may.
  [
    'server secret value',
    /\b(?:JWT_SECRET|DB_(?:APP|OWNER|PLATFORM)_PASSWORD|POSTGRES_SUPERUSER_PASSWORD|REDIS_PASSWORD|MEDIAMTX_API_PASSWORD|STREAM_TURN_SECRET|VIDEO_CREDENTIALS_KEY|LICENSE_PRIVATE_KEY_PEM|UAT_ACCESS_TOKEN)["'`]?\s*[=:]\s*["'`]?[A-Za-z0-9+/_.-]{8,}/,
  ],
  ['credentials in a URL', /\b(?:postgres(?:ql)?(?:\+asyncpg)?|redis|rediss|amqp):\/\/[^\s"'`/@]*:[^\s"'`/@]+@/],
  ['cloud access key', /\bAKIA[0-9A-Z]{16}\b/],
  ['local developer path', /(?:\/Users\/[A-Za-z0-9._-]+\/|[A-Z]:\\\\Users\\\\|\/opt\/esabong\/)/],
];
const textTypes = new Set(['.js', '.mjs', '.css', '.html', '.json', '.txt', '.webmanifest']);

const problems = [];
let files = 0;

function walk(dir, root) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const rel = relative(root, path);
    if (statSync(path).isDirectory()) {
      walk(path, root);
      continue;
    }
    files++;
    if (name.endsWith('.map')) problems.push(`${root}: source map file ${rel}`);
    if (/ 2\.[A-Za-z0-9]+$/.test(name)) problems.push(`${root}: Finder duplicate copied into the build: ${rel}`);
    if (name.startsWith('.')) problems.push(`${root}: hidden file in the build: ${rel}`);
    if (!textTypes.has(extname(name))) continue;
    const text = readFileSync(path, 'utf8');
    for (const [label, pattern] of forbidden) {
      const match = text.match(pattern);
      if (match) problems.push(`${root}: ${label} in ${rel} (…${match[0].slice(0, 40)}…)`);
    }
  }
}

for (const root of roots) {
  try {
    walk(root, root);
  } catch (err) {
    problems.push(`${root}: unreadable (${err.code ?? err.message})`);
  }
}

if (problems.length) {
  console.error(`Production bundle check FAILED (${problems.length}):\n  ${problems.join('\n  ')}`);
  process.exit(1);
}
console.log(`Production bundle check passed: ${files} files in ${roots.join(', ')} (no source maps, dev mode, secrets or local paths).`);

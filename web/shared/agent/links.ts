/** Unique share URLs for an agent. Registration does not assign the new account; the code is the username. */
export function agentShareLinks(origin: string, username: string): { player: string; application: string } {
  const ref = encodeURIComponent(username);
  const root = origin.replace(/\/$/, '');
  return {
    player: `${root}/register?ref=${ref}`,
    application: `${root}/register?ref=${ref}&apply=agent`,
  };
}

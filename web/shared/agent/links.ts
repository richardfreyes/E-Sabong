/** Unique share URLs for an agent. The code is the username; a player who registers through it is placed under this agent. */
export function agentShareLinks(origin: string, username: string): { player: string; application: string } {
  const ref = encodeURIComponent(username);
  const root = origin.replace(/\/$/, '');
  return {
    player: `${root}/register?ref=${ref}`,
    application: `${root}/register?ref=${ref}&apply=agent`,
  };
}

/** Share URLs use the inviter's globally unique account ID; legacy username codes still resolve when unambiguous. */
export function agentShareLinks(origin: string, referralCode: string, role = 'master_agent'): { player: string; application: string } {
  const ref = encodeURIComponent(referralCode);
  const root = origin.replace(/\/$/, '');
  return {
    player: `${root}/register?ref=${ref}`,
    application: role === 'sub_agent' ? `${root}/register?ref=${ref}` : `${root}/register?ref=${ref}&apply=${role === 'agent' ? 'sub_agent' : 'agent'}`,
  };
}

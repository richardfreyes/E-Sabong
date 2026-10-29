import type { EventItem, Match, MatchOutcome } from '@shared/models';

/** How a match or event reads to a player, independent of internal status names. */
export type PhaseTone = 'upcoming' | 'live' | 'finished' | 'closed' | 'cancelled';

export interface Phase {
  label: string;
  tone: PhaseTone;
}

export function matchPhase(m: Match): Phase {
  switch (m.status) {
    case 'in_progress':
      return { label: 'Live', tone: 'live' };
    case 'completed':
      return { label: 'Finished', tone: 'finished' };
    case 'cancelled':
      return { label: 'Cancelled', tone: 'cancelled' };
    default:
      return { label: 'Upcoming', tone: 'upcoming' };
  }
}

export function eventPhase(e: EventItem): Phase {
  switch (e.status) {
    case 'live':
      return { label: 'Live', tone: 'live' };
    case 'completed':
      return { label: 'Closed', tone: 'closed' };
    case 'cancelled':
      return { label: 'Cancelled', tone: 'cancelled' };
    default:
      return { label: 'Upcoming', tone: 'upcoming' };
  }
}

export const OUTCOME_TEXT: Record<MatchOutcome, string> = {
  meron: 'Meron wins',
  wala: 'Wala wins',
  draw: 'Draw',
  no_contest: 'No contest',
};

/** Result board colour class: red, blue, green, grey, plus the live and pending markers. */
export type BeadKind = 'meron' | 'wala' | 'draw' | 'void' | 'live' | 'pending';

export function beadKind(m: Match): BeadKind {
  if (m.status === 'cancelled') return 'void';
  if (m.result) return m.result.outcome === 'no_contest' ? 'void' : m.result.outcome;
  return m.status === 'in_progress' ? 'live' : 'pending';
}

export function isSettled(m: Match): boolean {
  return m.status === 'completed' || m.status === 'cancelled';
}

export interface DerbyStats {
  total: number;
  meron: number;
  wala: number;
  draw: number;
  voided: number;
  pct: (n: number) => number;
}

export function derbyStats(matches: Match[]): DerbyStats {
  const settled = matches.filter(isSettled);
  const count = (kind: BeadKind) => settled.filter((m) => beadKind(m) === kind).length;
  const total = settled.length;
  return {
    total,
    meron: count('meron'),
    wala: count('wala'),
    draw: count('draw'),
    voided: count('void'),
    pct: (n: number) => (total ? Math.round((n / total) * 1000) / 10 : 0),
  };
}

export function describeMatch(m: Match): string {
  if (m.status === 'cancelled') return `Fight ${m.match_number}, cancelled`;
  if (m.result) return `Fight ${m.match_number}, ${OUTCOME_TEXT[m.result.outcome]}`;
  return `Fight ${m.match_number}, ${matchPhase(m).label.toLowerCase()}`;
}

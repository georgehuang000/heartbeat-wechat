import {pursuitStages} from './pursuitOptions.js';

// A suggestion belongs to one observed conversation version, not just a name.
export function currentAssessment(value, identity, revision) {
  if (!value || value.contact_id !== identity || value.revision !== revision) return null;
  if (!['suggested', 'insufficient'].includes(value.status)) return null;
  if (value.stage !== 'unknown' && !pursuitStages.some(([id]) => id === value.stage)) return null;
  return value;
}

export function canAdoptAssessment(value, person) {
  if (!value || value.status !== 'suggested' || value.stage === 'unknown'
      || value.stage === person?.pursuit_stage || !value.evidence?.length) return false;
  return ['new', 'talking'].includes(value.stage) || person?.adult_context === true;
}

// Offline preview has no model or private chat. These are explicitly fictional.
export function demoAssessment(identity, messages, person) {
  const accepted = identity === 'demo-aning' && person?.adult_context === true;
  return {
    contact_id: identity, revision: 0, status: accepted ? 'suggested' : 'insufficient',
    stage: accepted ? 'accepted' : 'unknown', label: accepted ? '已接受' : '待确认',
    reason: accepted ? '记录中有你发出的邀请，以及对方明确答应；碰面地点还在商量。'
      : identity === 'demo-aning' ? '先确认双方成年的关系场景，再核对邀约进展。'
      : '只有一条对方分享，无法据此判断是否已经聊熟。',
    evidence: accepted ? messages.map(m => ({message_id: m.id, quote: m.text, role: m.role, timestamp: m.timestamp || ''})) : [],
    scope: {read_count: messages.length, text_count: messages.length, truncated: false},
    source: 'demo',
  };
}

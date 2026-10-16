import test from 'node:test';
import assert from 'node:assert/strict';
import {currentAssessment, canAdoptAssessment, demoAssessment} from '../src/assessmentData.js';
import {demoPursuitMessages, progressValue} from '../src/pursuitOptions.js';

const suggestion = {contact_id: 'A', revision: 9, status: 'suggested', stage: 'accepted',
  evidence: [{message_id: 'm', quote: '可以，在哪里碰面？'}]};

test('a suggestion never travels to a different contact or message revision', () => {
  assert.equal(currentAssessment(suggestion, 'B', 9), null);
  assert.equal(currentAssessment(suggestion, 'A', 10), null);
  assert.equal(currentAssessment({...suggestion, stage: 'likes-you'}, 'A', 9), null);
  assert.equal(currentAssessment(suggestion, 'A', 9), suggestion);
});

test('adoption requires evidence, a new value, and adult confirmation for date milestones', () => {
  assert.equal(canAdoptAssessment(suggestion, {adult_context: false}), false);
  assert.equal(canAdoptAssessment(suggestion, {adult_context: true}), true);
  assert.equal(canAdoptAssessment({...suggestion, evidence: []}, {adult_context: true}), false);
  assert.equal(canAdoptAssessment(suggestion, {adult_context: true, pursuit_stage: 'accepted'}), false);
  assert.equal(canAdoptAssessment({...suggestion, stage: 'talking'}, {adult_context: false}), true);
  assert.equal(progressValue('talking', false), 2);
  assert.equal(progressValue('accepted', false), 0);
});

test('fictional acceptance is not promoted to a settled appointment', () => {
  const messages = demoPursuitMessages('demo-aning');
  const value = demoAssessment('demo-aning', messages, {adult_context: true});
  assert.equal(value.stage, 'accepted');
  assert.deepEqual(value.evidence.map(e => e.role), ['self', 'other']);
  assert.ok(value.evidence.every(e => messages.some(m => m.id === e.message_id && m.text === e.quote)));
  assert.equal(demoAssessment('demo-aning', messages, {adult_context: false}).stage, 'unknown');
  assert.equal(demoAssessment('demo-xiaolin', demoPursuitMessages('demo-xiaolin'), {}).status, 'insufficient');
});

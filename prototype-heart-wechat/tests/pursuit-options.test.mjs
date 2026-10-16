import test from 'node:test';
import assert from 'node:assert/strict';
import {demoPursuitChoices, demoPursuitMessages, progressValue} from '../src/pursuitOptions.js';

test('the progress records confirmed events and has no inferred score', () => {
  assert.equal(progressValue('unknown', true), 0);
  assert.equal(progressValue('invited', true), 3);
  assert.equal(progressValue('scheduled', true), 5);
  assert.equal(progressValue('scheduled', false), 0);
  assert.equal(progressValue('fake-high-affinity', true), 0);
  assert.equal(progressValue('talking', true), 2); // Can move back.
});

test('unconfirmed adult context does not turn the invite goal into an invitation', () => {
  const ordinary = demoPursuitChoices({adult_context: false, pursuit_goal: 'invite'}, 'demo-xiaolin');
  const familiar = demoPursuitChoices({adult_context: true, pursuit_goal: 'get_to_know'}, 'demo-xiaolin');
  assert.deepEqual(ordinary, familiar);
});

test('accepted invitation keeps both speakers and answers arrangements for any goal', () => {
  assert.deepEqual(demoPursuitMessages('demo-aning').map(m => m.role), ['self', 'other']);
  const options = demoPursuitChoices({adult_context: true, pursuit_goal: 'get_to_know'}, 'demo-aning');
  assert.equal(options.length, 3);
  assert.deepEqual(options.map(c => c.label), ['确认碰面', '一起商量', '确认时间']);
});

test('demo options keep explanations separate from sendable text', () => {
  const options = demoPursuitChoices({adult_context: true, pursuit_goal: 'invite'}, 'demo-xiaolin');
  assert.equal(new Set(options.map(c => c.id)).size, 3);
  assert.ok(options.every(c => c.text && c.hint && c.label && !c.text.includes(c.hint)));
});

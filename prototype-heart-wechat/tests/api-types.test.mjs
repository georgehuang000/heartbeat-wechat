import test from 'node:test';
import assert from 'node:assert/strict';
import {API_TYPES,apiDescription} from '../src/apiTypes.js';
import {pageDefaults} from '../src/settingsPages.js';
import {replyActivity} from '../src/replyState.js';

test('three protocol labels identify the actual wire endpoints and reset preserves legacy mode',()=>{
 assert.equal(API_TYPES.length,3);
 assert.match(apiDescription('openai_chat'),/chat\/completions/);
 assert.match(apiDescription('openai_responses'),/\/responses/);
 assert.match(apiDescription('anthropic_messages'),/\/messages/);
 assert.equal(pageDefaults('connection').api_type,'openai_chat');
});
test('exhausted quota is not described as a transient rate limit',()=>{
 const activity=replyActivity({contact:{id:'A'},generation:{error:'HTTP 429',error_code:'quota_exceeded'}});
 assert.match(activity.label,/额度已用尽/);assert.equal(activity.action,'settings');assert.equal(activity.resume,true);
});

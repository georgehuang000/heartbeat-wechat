import test from 'node:test';
import assert from 'node:assert/strict';
import {HISTORY_MARKS,historyPosition,historyValue,parseHistoryCount,pageDefaults} from '../src/settingsPages.js';

test('history input preserves precise arbitrary counts and rejects invalid edits',()=>{
  for(const value of [10,11,137,1000,1680,3200,4999,5000]){
    assert.equal(parseHistoryCount(String(value)),value);
    assert.equal(historyValue(historyPosition(value)),value);
  }
  for(const value of ['',9,5001,100.5,'1e3',true,'not-a-count'])assert.equal(parseHistoryCount(value),null);
});
test('segmented slider is monotonic and gives the 1000-5000 interval most of the track',()=>{
  assert.equal(HISTORY_MARKS[0],10);assert.equal(HISTORY_MARKS.at(-1),5000);
  assert.equal(historyPosition(10),0);assert.equal(historyPosition(5000),1000);
  assert.ok(historyPosition(1000)<=400);
  for(let position=1;position<=1000;position++)assert.ok(historyValue(position)>historyValue(position-1));
});
test('context reset keeps other pages and encrypted credentials',()=>{
  const before={font_size:19,tone_balance:80,history_limit:5000,auto_generate:false,strict_quiet_wait:false,ask_before_assist:true,
    base_url:'https://example.invalid',protected_key:'keep-key',protected_asr_key:'keep-speech'};
  const after={...before,...pageDefaults('context')};
  assert.equal(after.history_limit,100);assert.equal(after.auto_generate,true);
  assert.equal(after.strict_quiet_wait,true);
  assert.equal(after.ask_before_assist,true);
  for(const key of ['font_size','tone_balance','base_url','protected_key','protected_asr_key'])assert.equal(after[key],before[key]);
});
test('connection reset omits all keys, current contact and calibration',()=>{
  const patch=pageDefaults('connection');
  assert.equal(patch.base_url,'https://api.deepseek.com');
  for(const key of ['api_key','asr_api_key','protected_key','protected_asr_key','visual_config','contact_id'])assert.ok(!(key in patch));
});
test('invalid reset page is rejected rather than falling back to a global reset',()=>{
  assert.throws(()=>pageDefaults('all'));
  assert.deepEqual(pageDefaults('appearance'),{});
});

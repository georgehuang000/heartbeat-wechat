import test from 'node:test';
import assert from 'node:assert/strict';
import {acceptServerState,replyUpdateLabel,replyActivity,emojiHelp} from '../src/replyState.js';

test('unanswered contacts get a question before model configuration or media status',()=>{
 const state={contact:{id:'A'},assistance:{enabled:false,needs_choice:true},
  generation:{status:'unconfigured'},attachment_status:{status:'processing',kind:'voice'}};
 assert.equal(replyActivity(state).action,'ask_contact');
 assert.equal(replyActivity(state).animated,false);
 assert.equal(replyActivity({...state,activity:{active:false}}).label,'后台已暂停');
});

test('all-contact mode overrides a remembered no without erasing the choice',()=>{
 const result=replyActivity({contact:{id:'A'},assistance:{enabled:true,needs_choice:false,decision:'no'},
  contact_preferences:{assistance_enabled:false},generation:{status:'ready'},choices:[{id:'ready'}]});
 assert.equal(result.label,'');
 assert.equal(result.action,undefined);
});

test('a disabled contact never displays stale choices or media work as active',()=>{
 const state={mode:'wechat',contact:{id:'A'},contact_preferences:{assistance_enabled:false},
  generation:{status:'generating'},attachment_status:{status:'processing',kind:'voice'}};
 const result=replyActivity(state);
 assert.equal(result.animated,false);
 assert.match(result.label,/尚未/);
 assert.equal(result.action,'settings');
 assert.equal(result.settingsPage,'context');
 assert.equal(replyActivity({...state,activity:{active:false}}).label,'后台已暂停');
});

test('background suspension and resume checks override stale activity animations',()=>{
 const state={contact:{id:'A'},generation:{status:'generating'},attachment_status:{status:'processing',kind:'voice'}};
 assert.deepEqual(replyActivity({...state,activity:{active:false,needs_refresh:true}}),{label:'后台已暂停',animated:false});
 assert.deepEqual(replyActivity({...state,activity:{active:true,needs_refresh:true}}),{label:'正在确认当前聊天…',animated:false});
});

test('manual generation mode does not pretend incoming messages started a model call',()=>{
 const state={contact:{id:'A'},settings:{auto_generate:false},generation:{status:'collecting'}};
 assert.equal(replyActivity(state).animated,false);
 assert.match(replyActivity(state).label,/等你准备好/);
 assert.equal(replyActivity({...state,generation:{status:'generating'}}).animated,true);
});

test('late poll cannot overwrite action state or restore stale choices',()=>{
 assert.equal(acceptServerState({revision:2,state_sequence:10},{revision:2,state_sequence:9}),false);
 assert.equal(acceptServerState({revision:2,state_sequence:10},{revision:3,state_sequence:11}),true);
 assert.equal(acceptServerState({revision:3},{revision:2}),false);
});
test('incoming burst shows the original context and keeps direct editable fill',()=>{
 const text=replyUpdateLabel({has_new_messages:true,new_message_count:3},{status:'generating'});
 assert.match(text,/3条/);assert.match(text,/上一段/);assert.match(text,/填入后编辑/);assert.match(text,/准备新回复/);
 assert.equal(replyUpdateLabel({has_new_messages:false},{status:'ready'}),'');
});
test('each selected emoji mode has its own explanation',()=>{
 assert.match(emojiHelp.off,/不加入/);assert.match(emojiHelp.natural,/参考聊天习惯/);assert.match(emojiHelp.lively,/活泼/);
});

test('progress shows plain activity and real failures expose retry',()=>{
 const reading=replyActivity({contact:{id:'A'},attachment_status:{status:'processing'},generation:{status:'reading_image'}});
 assert.equal(reading.label,'正在看图片…');assert.equal(reading.animated,true);
 const failed=replyActivity({contact:{id:'A'},attachment_status:{status:'error'}});
 assert.equal(failed.animated,false);assert.equal(failed.action,'retry_image');
 const connected=replyActivity({contact:{id:'A'},generation:{status:'collecting'},connection:{message:'已跟随当前聊天 · 数据库读取'}});
 assert.equal(connected.label,'正在想下一句…');assert.equal(connected.label.includes('数据库'),false);
 const voice=replyActivity({contact:{id:'A'},attachment_status:{status:'processing',kind:'voice'}});
 assert.equal(voice.label,'正在读这条语音…');
 const voiceFailure=replyActivity({contact:{id:'A'},attachment_status:{status:'error',kind:'voice'}});
 assert.equal(voiceFailure.action,'attachment');
 const ready=replyActivity({contact:{id:'A'},generation:{status:'ready'},choices:[{id:'choice-1'}]});
 assert.equal(ready.animated,false);assert.equal(ready.label,'');
});

test('billing failure points to settings even when the last message is an attachment',()=>{
 const state={contact:{id:'A'},messages:[{kind:'voice'}],generation:{status:'error',error_code:'insufficient_balance',error:'API 账户余额不足（HTTP 402）。'}};
 const activity=replyActivity(state);
 assert.match(activity.label,/余额不足/);
 assert.equal(activity.action,'settings');
 assert.equal(activity.animated,false);
 assert.equal(activity.actionLabel,'查看模型设置');
 assert.equal(replyActivity({...state,generation:{status:'error',error:'API 账户余额不足（HTTP 402）。'}}).action,'settings');
});

test('network failures provide retry while invalid model credentials point to settings',()=>{
 const state={contact:{id:'A'},messages:[{kind:'file'}]};
 assert.equal(replyActivity({...state,generation:{error:'API 密钥无效（HTTP 401）。'}}).action,'settings');
 const network=replyActivity({...state,generation:{error_code:'network',error:'无法连接 API'}});
 assert.match(network.label,/服务/);
 assert.equal(network.action,'generate');
});

test('holding choices never spins merely because text or a picture arrived',()=>{
 const state={contact:{id:'A'},choices:[{id:'choice-1'}],reply_batch:{has_new_messages:true},
  generation:{status:'holding'},attachment_status:{status:'processing'}};
 const activity=replyActivity(state);
 assert.equal(activity.animated,false);assert.equal(activity.action,'generate');
 assert.match(activity.label,/保留/);
});

test('ready choices stay available without a spinner while their replacement is generated or reviewed',()=>{
 for(const status of ['generating','reviewing']){
  const activity=replyActivity({contact:{id:'A'},choices:[{id:'choice-1'}],
   reply_batch:{has_new_messages:true},generation:{status}});
  assert.equal(activity.animated,false);assert.match(activity.label,/当前选项仍可用/);
  assert.equal(activity.action,undefined);
 }
});

test('a picture failure cannot hide balance failure or block a ready text reply',()=>{
 const state={contact:{id:'A'},attachment_status:{status:'error',kind:'image'},
  generation:{error_code:'insufficient_balance',error:'HTTP 402'}};
 assert.match(replyActivity(state).label,/余额不足/);
 assert.equal(replyActivity(state).resume,true);
 assert.equal(replyActivity({...state,choices:[{id:'choice-1'}],
  attachment_status:{status:'skipped',nonblocking:true},generation:{status:'ready'}}).animated,false);
});

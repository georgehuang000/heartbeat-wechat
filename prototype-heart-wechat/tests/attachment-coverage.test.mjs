import test from 'node:test';
import assert from 'node:assert/strict';
import {attachmentCoverage} from '../src/attachmentCoverage.js';
import {replyActivity} from '../src/replyState.js';
test('metadata never says full document/media was read',()=>{
 assert.match(attachmentCoverage({kind:'mini_program',content_known:true,content_scope:'metadata_only'}),/正文未取得/);
 assert.match(attachmentCoverage({kind:'location',content_scope:'metadata_only'}),/不代表发送者当前/);
 assert.match(attachmentCoverage({kind:'transfer',attachment:{status:'payment_event_only'}}),/未经核验/);
 assert.match(attachmentCoverage({kind:'emoji',visual_input:{content_scope:'first_frame_only'}}),/后续动画未提供/);
});
test('partial forwarded record retains concrete read/missing counts',()=>{
 assert.match(attachmentCoverage({kind:'note',attachment:{read_count:2,missing_count:1,status:'partial'}}),/已读2条.*缺失1条/);
});
test('waiting is quiet and closing explains the optional single choice',()=>{
 const state={contact:{id:'a'},choices:[],generation:{status:'ready',conversation_state:'wait',retained_closing:{text:'晚安'}}};
 assert.deepEqual(replyActivity(state),{label:'你已收尾，等对方接话。',animated:false});
 state.choices=[{}];state.generation.conversation_state='close';
 assert.match(replyActivity(state).label,/可以不发/);
 state.choices=[];state.generation.status='reviewing';assert.equal(replyActivity(state).animated,true);
});

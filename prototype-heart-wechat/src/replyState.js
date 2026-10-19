// A delayed poll/action response must not roll the displayed transcript back.
export function acceptServerState(previous, incoming) {
 if(!incoming)return false;
 if(!previous)return true;
 if(Number.isInteger(incoming.state_sequence)&&Number.isInteger(previous.state_sequence))
  return incoming.state_sequence>=previous.state_sequence;
 return (incoming.revision??0)>=(previous.revision??0);
}

export function replyUpdateLabel(batch, generation) {
 if(batch?.has_new_messages){
  const count=batch.new_message_count;
  const source=batch.source_excerpt?`「${batch.source_excerpt}」`:'上一段';
  return `对方又发了${Number.isInteger(count)?count+'条':'新'}消息；这些选项针对${source}，可填入后编辑。${generation?.status==='generating'?'正在准备新回复…':''}`;
 }
 return '';
}

export const emojiHelp={
 off:'关闭：回复不加入表情或颜文字。',
 natural:'自然：参考聊天习惯，适量加入，不必每句都带。',
 lively:'活泼：增加表情和颜文字，遇到严肃或难过的话题会收敛。',
};

function serviceFailure(generation){
 if(!generation.error)return null;
 const code=generation.error_code,error=String(generation.error);
 if(code==='quota_exceeded')
  return {label:'模型额度已用尽，已暂停自动生成。',animated:false,action:'settings',actionLabel:'查看模型设置',resume:true};
 if(code==='output_limit')
  return {label:'模型输出未完整，请检查模型或输出配置。',animated:false,action:'settings',resume:true};
 if(code==='refused')
  return {label:'模型没有提供这轮回复，可以调整方向再试。',animated:false,action:'settings'};
 if(code==='insufficient_balance'||/HTTP\s*402|余额不足/.test(error))
  return {label:'模型账户余额不足，已暂停自动生成。',animated:false,action:'settings',actionLabel:'查看模型设置',resume:true};
 if(['authentication','access_denied','configuration'].includes(code)||/HTTP\s*(401|403|404)|API 密钥无效/.test(error))
  return {label:'模型连接配置需要检查，已暂停自动生成。',animated:false,action:'settings',resume:true};
 if(code==='rate_limit'||/HTTP\s*429/.test(error))
  return {label:'模型请求受限，已暂停自动重试，可稍后再试。',animated:false,action:'generate'};
 if(['timeout','network','service_unavailable','service_error'].includes(code)||/响应超时|无法连接 API|HTTP\s*5\d\d/.test(error))
  return {label:'回复服务暂时无法响应，可以稍后再试。',animated:false,action:'generate'};
 return null;
}

export function replyActivity(state){
 if(state?.activity?.active===false){
  const labels={panel_hidden:'心动微信已收起，辅助已暂停。',wechat_hidden:'请同时展开微信和心动微信。',
   window_obscured:'窗口被遮挡，辅助已暂停。',window_hidden:'窗口不可见，辅助已暂停。',
   visibility_unknown:'暂时无法确认窗口状态，已暂停。'};
  return {label:labels[state.activity.reason]||'后台已暂停',animated:false};
 }
 const connection=state?.connection;
 if(connection?.can_read===false&&connection.message&&
    !['connected','disconnected','connecting'].includes(connection.status))
  return {label:connection.message,animated:false,action:'connect',actionLabel:'重新识别聊天'};
 if(state?.activity?.needs_refresh)return {label:'正在确认当前聊天…',animated:false};
 if(state?.paused)return {label:'已暂停',animated:false};
 if(!state?.contact)return {label:'等待当前聊天…',animated:false};
 if(state.assistance?.needs_choice)return {label:'',animated:false,action:'ask_contact'};
 if(state.assistance?.enabled===false||state.mode!=='demo'&&!state.assistance&&state.contact_preferences?.assistance_enabled===false)
  return {label:'',animated:false,action:'enable_contact'};
 const attachment=state.attachment_status,generation=state.generation||{};
 if(generation.status==='interrupted')return {label:'上次生成已中断，可以重新生成。',animated:false,action:'generate'};
 const failure=serviceFailure(generation);
 if(failure)return failure;
 if(state.choices?.length&&['holding','collecting'].includes(generation.status))
  return {label:state.reply_batch?.has_new_messages?'有新消息，先保留这组选项。':'先保留这组选项。',animated:false,action:'generate'};
 if(state.choices?.length&&['generating','reviewing'].includes(generation.status))
  return {label:'正在准备新回复，当前选项仍可用。',animated:false};
 if(attachment?.status==='error'&&!attachment.nonblocking)return attachment.kind==='voice'
  ?{label:'这条语音暂时读不到，可以补充文字。',animated:false,action:'attachment'}
  :{label:'这张图暂时读不到，可以再试一次。',animated:false,action:'retry_image'};
 if(['processing','retrying'].includes(attachment?.status)&&attachment.kind==='voice')
  return {label:'正在读这条语音…',animated:true};
 if(['processing','retrying'].includes(attachment?.status)||generation.status==='reading_image')
  return state.choices?.length?{label:'先保留这组选项。',animated:false,action:'generate'}:{label:'正在看图片…',animated:true};
 if(generation.status==='unconfigured')return {label:'在设置里连接模型，即可开始。',animated:false,action:'settings'};
 if(generation.status==='reviewing')return {label:'再斟酌一下…',animated:true};
 if(generation.status==='collecting'&&state.settings?.auto_generate===false)
  return {label:'等你准备好，再想下一句。',animated:false,action:'generate'};
 if(state.waiting_for_change&&['idle','collecting'].includes(generation.status))
  return {label:'等待新消息…',animated:false};
 if(['generating','collecting'].includes(generation.status))return {label:'正在想下一句…',animated:true};
 if(generation.error){
  const error=String(generation.error),code=generation.error_code;
  if(code==='insufficient_balance'||/HTTP\s*402|余额不足/.test(error))
   return {label:'模型账户余额不足，暂时无法生成回复。',animated:false,action:'settings',actionLabel:'查看模型设置'};
  if(['authentication','access_denied','configuration'].includes(code)||/HTTP\s*(401|403|404)|API 密钥无效/.test(error))
   return {label:'模型连接配置需要检查，请打开设置。',animated:false,action:'settings'};
  if(['timeout','network','service_unavailable','service_error','rate_limit'].includes(code)||/响应超时|无法连接 API|HTTP\s*(429|5\d\d)/.test(error))
   return {label:'回复服务暂时无法响应，可以稍后再试。',animated:false,action:'generate'};
  const kind=state.messages?.at(-1)?.kind;
  if(['voice','video','forward','file'].includes(kind))return {label:'这条附件还需要补充内容。',animated:false,action:'attachment'};
  return {label:'这次没想好，可以再试一次。',animated:false,action:'generate'};
 }
 if(state.reply_batch?.has_new_messages&&state.choices?.length)return {label:'有新消息，先保留这组选项。',animated:false,action:'generate'};
 if(generation.status==='ready')return {label:generation.conversation_state==='close'?'轻松收个尾，也可以不发。':state.choices?.length?'':generation.retained_closing?'你已收尾，等对方接话。':'等对方接话，不必补发。',animated:false};
 return {label:state.settings?.auto_generate===false?'等你准备好，再想下一句。':'正在想下一句…',animated:state.settings?.auto_generate!==false};
}

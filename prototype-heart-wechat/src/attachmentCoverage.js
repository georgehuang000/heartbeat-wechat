export const attachmentNames={voice:'语音',image:'图片',emoji:'表情包',forward:'合并聊天记录',note:'收藏笔记',card:'名片',call:'通话记录',file:'文件',link:'链接',video:'视频',location:'分享位置',live_location:'实时位置',mini_program:'小程序',music:'音乐分享',channel:'视频号',live:'直播卡片',pat:'拍一拍',transfer:'转账记录',red_envelope:'红包记录',payment:'支付记录',announcement:'群公告',solitaire:'群接龙',application_card:'应用卡片',system:'系统事件',other:'附件'};

export function attachmentCoverage(message){
 const a=message.attachment||{},v=message.visual_input;
 if(v)return v.content_scope==='first_frame_only'?'已加入动图第一帧；后续动画未提供。':'图片已加入回复，保留原发送方与时间。';
 if(['forward','note'].includes(message.kind))return `已读${a.read_count||0}条引用内容；${a.status==='complete'?'正文已取得。':`缺失${a.missing_count??'未知'}条或附件，仅依据已读部分。`}`;
 if(a.status==='payment_event_only')return '已读取支付消息事件；领取、到账和当前钱包状态未经核验。';
 if(message.kind==='video')return '只提供视频发送事件，不上传视频画面或声音。';
 if(message.content_scope==='event_only'||['call_event_only','event_only'].includes(a.status))return '只读取事件，不推断通话内容或实时位置。';
 if(message.content_scope==='metadata_only'||a.status==='card_metadata_only')return message.kind==='location'?'读到了分享的位置点，不代表发送者当前所在位置。':'仅读取卡片标题、描述等信息；网页、文件或媒体正文未取得。';
 if(message.content_known)return '已有可读内容，沿用原消息时间。';
 if(message.kind==='voice')return '尚未取得微信转文字结果；转文字后可重新读取，备用语音API可选。';
 return '这条附件的正文尚未取得，可以补充希望讨论的内容。';
}

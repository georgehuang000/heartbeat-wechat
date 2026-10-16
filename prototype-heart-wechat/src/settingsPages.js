export const SETTINGS_DEFAULTS = {
  history_limit:100,emoji_mode:'natural',reply_style:'playful',tone_balance:30,
  font_size:16,dialogue_opacity:88,typewriter:true,text_speed:70,inner_mode:false,
  maximized_mode:'hide',overlay_opacity:22,auto_generate:true,strict_quiet_wait:true,ask_before_assist:false,follow_window:true,
  media_policy:'ask',auto_images:true,image_limit:3,sticker_limit:0,auto_voice:true,
  base_url:'https://api.deepseek.com',model:'deepseek-flash',
  api_type:'openai_chat',max_tokens:900,
  asr_base_url:'https://api.siliconflow.cn/v1',asr_model:'FunAudioLLM/SenseVoiceSmall',
};
export const SETTINGS_PAGES = [
  ['appearance','形象'],['replies','台词'],['context','上下文'],['display','显示'],['connection','连接'],
];
export const HISTORY_MARKS = [10,100,1000,2500,5000];
const HISTORY_SCALE = [[0,10],[90,100],[300,1000],[1000,5000]];
const PAGE_KEYS = {
  appearance:[],
  replies:['reply_style','tone_balance','emoji_mode','inner_mode'],
  context:['history_limit','auto_generate','strict_quiet_wait','auto_images','image_limit','sticker_limit','auto_voice','media_policy'],
  display:['font_size','dialogue_opacity','typewriter','text_speed','maximized_mode','overlay_opacity','follow_window'],
  connection:['base_url','model','api_type','max_tokens','asr_base_url','asr_model'],
};
export function pageDefaults(page) {
  if (!Object.hasOwn(PAGE_KEYS,page)) throw new Error('设置页面不存在');
  return Object.fromEntries(PAGE_KEYS[page].map(key=>[key,SETTINGS_DEFAULTS[key]]));
}
export function parseHistoryCount(value) {
  const text=String(value).trim();
  if(!/^\d+$/.test(text))return null;
  const count=Number(text);
  return Number.isInteger(count)&&count>=10&&count<=5000?count:null;
}
export function historyPosition(value) {
  const count=parseHistoryCount(value)??100;
  for(let i=1;i<HISTORY_SCALE.length;i++){
    const [end,n]=HISTORY_SCALE[i], [start,previous]=HISTORY_SCALE[i-1];
    if(count<=n)return start+(count-previous)/(n-previous)*(end-start);
  }
  return 1000;
}
export function historyValue(position) {
  const value=Math.max(0,Math.min(1000,Number(position)||0));
  for(let i=1;i<HISTORY_SCALE.length;i++){
    const [end,n]=HISTORY_SCALE[i], [start,previous]=HISTORY_SCALE[i-1];
    if(value<=end)return Math.round(previous+(value-start)/(end-start)*(n-previous));
  }
  return 5000;
}

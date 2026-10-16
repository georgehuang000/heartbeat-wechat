export const API_TYPES = [
  ['openai_chat','OpenAI Chat Completions','/v1/chat/completions'],
  ['openai_responses','OpenAI Responses','/v1/responses'],
  ['anthropic_messages','Anthropic Messages','/v1/messages'],
];
export function apiDescription(value){
  const found=API_TYPES.find(([id])=>id===value);
  return found?`${found[1]} · ${found[2]}`:'API 类型尚未识别';
}

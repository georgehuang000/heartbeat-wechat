"""Stateless wire adapters for the three explicitly selectable chat protocols."""
from copy import deepcopy
from urllib.parse import urlsplit

API_TYPES = {'openai_chat', 'openai_responses', 'anthropic_messages'}
ENDPOINTS = {'openai_chat': '/chat/completions', 'openai_responses': '/responses',
             'anthropic_messages': '/messages'}


class ProtocolError(ValueError):
    def __init__(self, message, code='configuration'):
        super().__init__(message)
        self.code = code


def api_type(config):
    value = config.get('api_type', 'openai_chat')
    if not isinstance(value, str) or value not in API_TYPES:
        raise ProtocolError('API 类型无效，请选择已支持的接口协议。')
    return value


def api_base(base):
    for endpoint in sorted(ENDPOINTS.values(), key=len, reverse=True):
        if base.endswith(endpoint):
            base = base[:-len(endpoint)]
            break
    path = urlsplit(base).path.rstrip('/')
    if not path or (urlsplit(base).hostname == 'api.deepseek.com' and path == '/anthropic'):
        base += '/v1'
    return base


def headers_for(protocol, key):
    headers = {'Content-Type': 'application/json'}
    if protocol == 'anthropic_messages':
        headers['anthropic-version'] = '2023-06-01'
        if key:
            headers['x-api-key'] = key
    elif key:
        headers['Authorization'] = 'Bearer ' + key
    return headers


def wire_request(protocol, body):
    if protocol == 'openai_chat':
        return ENDPOINTS[protocol], deepcopy(body)
    system, messages = [], []
    for message in body.get('messages', []):
        role, content = message.get('role'), message.get('content')
        if role in {'system', 'developer'}:
            if not isinstance(content, str):
                raise ProtocolError('系统指令格式无效。')
            system.append(content)
            continue
        if role not in {'user', 'assistant'}:
            raise ProtocolError('这类消息角色暂不支持。')
        if isinstance(content, list):
            parts = []
            for part in content:
                if part.get('type') == 'text':
                    parts.append({'type': 'input_text' if protocol == 'openai_responses' else 'text',
                                  'text': part['text']})
                elif part.get('type') == 'image_url':
                    image = part['image_url']; url = image['url']
                    if protocol == 'openai_responses':
                        parts.append({'type': 'input_image', 'image_url': url,
                                      'detail': image.get('detail', 'auto')})
                    else:
                        header, sep, data = url.partition(',')
                        if not sep or not header.startswith('data:image/') or not header.endswith(';base64'):
                            raise ProtocolError('Messages 图片须使用本程序已读取的本地图像。')
                        parts.append({'type': 'image', 'source': {'type': 'base64',
                            'media_type': header[5:-7], 'data': data}})
                else:
                    raise ProtocolError('这类输入内容暂不支持。')
            content = parts
        messages.append({'role': role, 'content': content})
    # Internal prompts already require JSON; Messages has no json_object flag.
    extra = {key: deepcopy(value) for key, value in body.items()
             if key not in {'messages', 'response_format', 'max_tokens', 'temperature', 'model'}}
    result = {'model': body['model'], **extra}
    fmt = body.get('response_format', {})
    if protocol == 'openai_responses':
        result.update(input=messages, instructions='\n\n'.join(system),
                      max_output_tokens=body['max_tokens'], store=False)
        # Sampling overrides are optional and rejected by some reasoning models.
        # The user's style is already part of the prompt.
        if fmt.get('type') == 'json_object':
            result['text'] = {'format': {'type': 'json_object'}}
        elif fmt.get('type') == 'json_schema':
            result['text'] = {'format': {'type': 'json_schema', **fmt['json_schema']}}
    else:
        result.update(messages=messages, system='\n\n'.join(system), max_tokens=body['max_tokens'])
        if fmt.get('type') == 'json_schema':
            result['output_config'] = {'format': {'type': 'json_schema', 'schema': fmt['json_schema']['schema']}}
        # Temperature is optional; omitting it also works on Claude models that
        # reject sampling overrides. User style is carried in the prompt.
    return ENDPOINTS[protocol], result


def normalized_response(protocol, data):
    if protocol == 'openai_chat':
        choices = data.get('choices')
        if not isinstance(choices, list):
            raise ProtocolError('服务返回格式与所选 API 类型不一致。')
        for choice in choices:
            if isinstance(choice, dict) and isinstance(choice.get('message'), dict) and choice['message'].get('refusal'):
                raise ProtocolError('模型没有提供这轮回复，请调整方向后重试。', 'refused')
        return data
    if protocol == 'openai_responses':
        if data.get('status') == 'failed' or data.get('error'):
            raise ProtocolError('模型服务没有完成这次请求。', 'service_error')
        output = data.get('output', [])
        if not isinstance(output, list):
            raise ProtocolError('服务返回格式与所选 API 类型不一致。')
        blocks = []
        for item in output:
            if not isinstance(item, dict) or item.get('type') != 'message' or item.get('role') != 'assistant':
                continue
            content = item.get('content', [])
            if not isinstance(content, list):
                raise ProtocolError('服务返回格式与所选 API 类型不一致。')
            blocks.extend(part for part in content if isinstance(part, dict))
        text_type = 'output_text'
        finish = 'length' if data.get('status') == 'incomplete' else 'stop'
    else:
        if data.get('stop_reason') == 'refusal':
            raise ProtocolError('模型没有提供这轮回复，请调整方向后重试。', 'refused')
        blocks = data.get('content', [])
        text_type = 'text'
        finish = 'length' if data.get('stop_reason') == 'max_tokens' else 'stop'
    if not isinstance(blocks, list):
        raise ProtocolError('服务返回格式与所选 API 类型不一致。')
    if any(part.get('type') == 'refusal' for part in blocks if isinstance(part, dict)):
        raise ProtocolError('模型没有提供这轮回复，请调整方向后重试。', 'refused')
    text = '\n'.join(part['text'] for part in blocks if isinstance(part, dict)
                     and part.get('type') == text_type and isinstance(part.get('text'), str))
    if not text:
        if finish == 'length':
            raise ProtocolError('模型输出达到长度上限，未取得完整回复；请检查模型或输出配置。', 'output_limit')
        raise ProtocolError('未取得该协议的文本回复，请检查 API 类型和模型。')
    return {'id': data.get('id'), 'model': data.get('model'),
            'choices': [{'message': {'content': text}, 'finish_reason': finish}],
            'usage': usage_from_response(data, protocol)}


def usage_from_response(data, protocol):
    raw = data.get('usage') if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    numeric = lambda value: type(value) is int and value >= 0
    if protocol == 'openai_chat':
        result = {key: raw[key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens',
            'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens') if numeric(raw.get(key))}
        details = raw.get('prompt_tokens_details')
        cached = details.get('cached_tokens') if isinstance(details, dict) else None
        output_details = raw.get('completion_tokens_details')
    else:
        result = {}
        if numeric(raw.get('input_tokens')):
            prompt = raw['input_tokens']
            if protocol == 'anthropic_messages':
                prompt += sum(raw.get(key, 0) for key in ('cache_creation_input_tokens', 'cache_read_input_tokens')
                              if numeric(raw.get(key)))
            result['prompt_tokens'] = prompt
        if numeric(raw.get('output_tokens')):
            result['completion_tokens'] = raw['output_tokens']
        details = raw.get('input_tokens_details')
        cached = raw.get('cache_read_input_tokens') if protocol == 'anthropic_messages' else (
            details.get('cached_tokens') if isinstance(details, dict) else None)
        output_details = raw.get('output_tokens_details')
        if protocol == 'anthropic_messages' and numeric(raw.get('cache_creation_input_tokens')):
            result['prompt_cache_creation_tokens'] = raw['cache_creation_input_tokens']
    if numeric(cached):
        result.setdefault('prompt_cache_hit_tokens', cached)
        if cached <= result.get('prompt_tokens', -1):
            result.setdefault('prompt_cache_miss_tokens', result['prompt_tokens'] - cached)
    if 'prompt_tokens' in result and 'completion_tokens' in result:
        result.setdefault('total_tokens', result['prompt_tokens'] + result['completion_tokens'])
    if isinstance(output_details, dict):
        reasoning = output_details.get('reasoning_tokens', output_details.get('thinking_tokens'))
        if numeric(reasoning):result['reasoning_tokens'] = reasoning
    return result

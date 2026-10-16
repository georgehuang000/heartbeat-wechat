"""Evaluate original synthetic scenarios; default mode never loads credentials or calls APIs."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import galchat.engine as engine_module
from galchat.engine import ReplyEngine, ReplyEngineError

CORPUS_PATH = ROOT / 'evals/reply-usefulness.json'


def load_cases(path=None):
    corpus = json.loads((path or CORPUS_PATH).read_text(encoding='utf-8'))
    if corpus.get('synthetic_only') is not True:
        raise ValueError('只允许标明为虚构的场景。')
    ids = set()
    for case in corpus['cases']:
        if case['id'] in ids or not case['messages'] or not case['expected']:
            raise ValueError('场景ID重复、消息或评价标准为空。')
        ids.add(case['id'])
        for message in case['messages']:
            if message['role'] not in {'self', 'other'} or not isinstance(message['text'], str):
                raise ValueError('场景消息格式错误。')
    return corpus


def case_context(case):
    return {
        'contact_id': 'synthetic-reply-quality', 'contact_name': '虚构小林',
        'recent': [{
            'id': f"{case['id']}-{i}", 'timestamp': f'2026-10-12T10:00:{i:02d}',
            'source': 'synthetic-evaluation', **message,
        } for i, message in enumerate(case['messages'])],
        'reply_preferences': {
            'reply_style': 'playful', 'tone_balance': 30, 'emoji_mode': 'off',
            'adult_context': case.get('adult_context', False),
            'pursuit_goal': case.get('pursuit_goal', 'get_to_know'),
            'pursuit_stage': case.get('pursuit_stage', 'unknown'),
        },
    }


def screen_choices(choices, case):
    """Exact phrases are review flags, not a semantic score or a production filter."""
    flags = []
    for choice in choices:
        for phrase in case.get('reject_phrases', []):
            if phrase in choice['text']:
                flags.append({'choice_id': choice['id'], 'phrase': phrase})
        if len(choice['hint']) > 30:
            flags.append({'choice_id': choice['id'], 'reason': 'hint_over_30_characters'})
    return flags


def main():
    source_parser = argparse.ArgumentParser(add_help=False)
    source_parser.add_argument('--corpus', type=Path, default=CORPUS_PATH, help='Original synthetic scenario JSON')
    source_args, _ = source_parser.parse_known_args()
    corpus_path = source_args.corpus if source_args.corpus.is_absolute() else ROOT / source_args.corpus
    corpus = load_cases(corpus_path)
    parser = argparse.ArgumentParser(description=__doc__, parents=[source_parser])
    parser.add_argument('--case', action='append', choices=[c['id'] for c in corpus['cases']])
    parser.add_argument('--live', action='store_true', help='Call configured API with synthetic cases only')
    parser.add_argument('--verify-replies', action='store_true', help='Use the production semantic review and bounded repair')
    parser.add_argument('--trace-review', action='store_true', help='Preserve synthetic generation/review responses for diagnosis')
    parser.add_argument('--review-thinking', action='store_true', help='Research-only DeepSeek low-effort thinking for review; production settings unchanged')
    parser.add_argument('--output', help='Save a new evidence report; required with --live')
    parser.add_argument('--prompt-file', type=Path, help='Use a saved baseline prompt in this process only')
    args = parser.parse_args()
    cases = [case for case in corpus['cases'] if not args.case or case['id'] in args.case]
    output_path = (ROOT / args.output).resolve() if args.output else None
    if args.live and output_path is None:
        parser.error('--live requires --output')
    if args.review_thinking and not (args.live and args.verify_replies):
        parser.error('--review-thinking requires --live --verify-replies')
    if output_path and output_path.exists():
        parser.error('报告已存在，请指定新文件以保留原始证据。')
    if args.prompt_file:
        prompt_path = args.prompt_file if args.prompt_file.is_absolute() else ROOT / args.prompt_file
        engine_module.SYSTEM_PROMPT = prompt_path.read_text(encoding='utf-8')
    report = {
        'synthetic_only': True, 'private_history_sent': False, 'wechat_operated': False,
        'api_called': args.live, 'generated_at': datetime.now().astimezone().isoformat(),
        'review_thinking_research_only': args.review_thinking,
        'prompt_sha256': hashlib.sha256(engine_module.SYSTEM_PROMPT.encode('utf-8')).hexdigest(),
        'system_prompt': engine_module.SYSTEM_PROMPT,
        'engine_sha256': hashlib.sha256((ROOT / 'src/galchat/engine.py').read_bytes()).hexdigest(),
        'corpus_sha256': hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        'pursuit_sha256': hashlib.sha256((ROOT / 'src/galchat/pursuit.py').read_bytes()).hexdigest(),
        'rubric': corpus['rubric'], 'semantic_review': 'pending' if args.live else 'not_run',
        'results': [],
    }
    config = None
    if args.live:
        from galchat.settings import Settings
        config = Settings(ROOT / 'data').model_config()
        config['verify_replies'] = args.verify_replies
        if not config.get('api_key'):
            parser.error('未配置API密钥，未发送测试请求。')
        if args.review_thinking and (urlsplit(config['base_url']).hostname != 'api.deepseek.com' or config['model'] != 'deepseek-flash'):
            parser.error('review thinking probe only supports the verified official DeepSeek endpoint/model')
    engine = ReplyEngine()
    traces=[]
    if args.trace_review or args.review_thinking:
        original_request=engine._request
        def request_trace(method,url,headers,timeout,body=None):
            reviewing = bool(body and '事实审稿人' in body['messages'][0]['content'])
            if reviewing and args.review_thinking:
                body = {**body, 'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low', 'max_tokens': 8192}
                body.pop('temperature', None)
            started = time.monotonic()
            response=original_request(method,url,headers,timeout,body)
            # Preserve final receipts and usage, not model reasoning text.
            saved = json.loads(json.dumps(response))
            for c in saved.get('choices', []):
                c.get('message', {}).pop('reasoning_content', None)
            traces.append({'phase':'review' if reviewing else 'generation',
                           'elapsed_ms': round((time.monotonic()-started)*1000),
                           'thinking': bool(reviewing and args.review_thinking), 'response':saved})
            return response
        engine._request=request_trace
    for case in cases:
        traces.clear()
        context = case_context(case)
        row = {'case': case['id'], 'category': case['category'],
               'intent': case.get('intent', ''), 'input': case['messages'],
               'reply_preferences': context['reply_preferences'],
               'pursuit': engine._payload_context(context)['pursuit'],
               'expected': case['expected']}
        if args.live:
            try:
                generated = engine.generate(context, config, intent=case.get('intent', ''))
                row.update(generated)
                row['review_flags'] = screen_choices(generated['choices'], case)
                print(json.dumps({'case': case['id'], 'choices': generated['choices'],
                                  'review_flags': row['review_flags']}, ensure_ascii=False), flush=True)
            except ReplyEngineError as error:
                row['error'] = str(error)
                print(json.dumps({'case': case['id'], 'error': str(error)}, ensure_ascii=False), flush=True)
        else:
            payload = engine._payload_context(context)
            row['included_message_count'] = len(payload['recent'])
        report['results'].append(row)
        if args.trace_review:row['synthetic_responses']=list(traces)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Refuse concurrent overwrite while creating the first checkpoint.
            with output_path.open('x' if len(report['results']) == 1 else 'w', encoding='utf-8') as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
    errors = sum('error' in row for row in report['results'])
    print(json.dumps({'cases': len(cases), 'api_called': args.live, 'errors': errors,
                      'semantic_review': report['semantic_review']}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())

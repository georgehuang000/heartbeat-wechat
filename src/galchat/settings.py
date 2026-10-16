from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from .conversation_identity import HISTORY_LIMITS

DEFAULTS = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "api_type": "openai_chat",
    "auto_generate": True,
    "strict_quiet_wait": True,
    "ask_before_assist": False,
    "follow_window": True,
    "temperature": 0.8,
    "max_tokens": 900,
    "timeout": 120,
    "visual_config": None,
    "history_limit": 100,
    "emoji_mode": "natural",
    "reply_style": "playful",
    "tone_balance": 30,
    "maximized_mode": "hide",
    "overlay_opacity": 22,
    "font_size": 16,
    "dialogue_opacity": 88,
    "typewriter": True,
    "text_speed": 70,
    "inner_mode": False,
    "media_policy": "ask",
    "auto_images": True,
    "image_limit": 3,
    "sticker_limit": 0,
    "reply_hold_seconds": 30,  # Legacy compatibility; no longer gates automatic updates.
    "auto_voice": True,
    "asr_base_url": "https://api.siliconflow.cn/v1",
    "asr_model": "FunAudioLLM/SenseVoiceSmall",
}


class Settings:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "settings.json"
        self.values = dict(DEFAULTS)
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.values.update({k: v for k, v in saved.items() if k in DEFAULTS or k in {"protected_key", "protected_asr_key"}})
            # Replace only the abandoned, keyless prototype defaults.
            if (self.values["base_url"] in {"http://127.0.0.1:18765/v1", "http://127.0.0.1:18765"}
                    and self.values["model"] == "qwen3-4b" and not self.values.get("protected_key")):
                self.values.update(base_url=DEFAULTS["base_url"], model=DEFAULTS["model"])
                self.save({})

    def public(self) -> dict:
        return {**{k: v for k, v in self.values.items() if k not in {"protected_key", "protected_asr_key"}},
                "asr_ready": bool(self.values.get('protected_asr_key')),
                "api_key_set": bool(self.values.get("protected_key")),
                "api_key_required": True,
                "api_ready": bool(self.values.get("protected_key"))}

    def model_config(self) -> dict:
        config = dict(self.values)
        config.pop("visual_config", None)
        config.pop('protected_asr_key', None)
        protected = config.pop("protected_key", "")
        config["api_key"] = ""
        if protected:
            import win32crypt
            config["api_key"] = win32crypt.CryptUnprotectData(base64.b64decode(protected), None, None, None, 0)[1].decode()
        if urlparse(config["base_url"]).hostname == "api.deepseek.com":
            config["provider_extra"] = ({"reasoning": {"effort": "none"}}
                if config['api_type'] == 'openai_responses' else {"thinking": {"type": "disabled"}})
        return config

    def asr_config(self):
        protected = self.values.get('protected_asr_key')
        key = ''
        if protected:
            import win32crypt
            key = win32crypt.CryptUnprotectData(base64.b64decode(protected), None, None, None, 0)[1].decode()
        return {'base_url': self.values['asr_base_url'], 'model': self.values['asr_model'],
                'api_key': key, 'timeout': self.values['timeout']}

    def save(self, values: dict) -> dict:
        candidate = dict(self.values)
        if "visual_config" in values:
            from .visual_wechat import VisualWeChatAdapter
            candidate["visual_config"] = VisualWeChatAdapter(lambda: {}).configure(values["visual_config"])
        for field in ("auto_generate", "strict_quiet_wait", "ask_before_assist", "follow_window", "typewriter", "inner_mode", "auto_images", "auto_voice"):
            if field in values:
                if not isinstance(values[field], bool):
                    raise ValueError("开关配置无效")
                candidate[field] = values[field]
        for field, allowed in {'history_limit': HISTORY_LIMITS,
                               'api_type': ('openai_chat', 'openai_responses', 'anthropic_messages'),
                               'image_limit': (0, 1, 3, 5),
                               'sticker_limit': (0, 1),
                               'reply_hold_seconds': (15, 30, 60),
                               'emoji_mode': ('off','natural','lively'),
                               'reply_style': ('playful','serious','absurd'),
                               'media_policy': ('ask', 'auto', 'off'),
                               'maximized_mode': ('hide','overlay')}.items():
            if field in values:
                if (values[field] not in allowed or isinstance(values[field], bool)
                        or field in {'history_limit', 'image_limit', 'sticker_limit', 'reply_hold_seconds'} and not isinstance(values[field], int)):
                    raise ValueError(f'{field}配置无效')
                candidate[field] = values[field]
        for field, bounds in {'tone_balance': (0,100), 'max_tokens': (256,4096), 'overlay_opacity': (10,60),
                               'font_size': (14,20), 'dialogue_opacity': (35,100), 'text_speed': (10,100)}.items():
            if field in values:
                value = values[field]
                if not isinstance(value, int) or isinstance(value, bool) or not bounds[0] <= value <= bounds[1]:
                    raise ValueError(f'{field}配置无效')
                candidate[field] = value
        for endpoint_field in ('base_url', 'asr_base_url'):
            if endpoint_field not in values:
                continue
            url = str(values[endpoint_field]).strip().rstrip("/")
            parsed = urlparse(url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("请填写 http 或 https 模型地址")
            if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("远程模型请使用 https 地址")
            candidate[endpoint_field] = url
        for model_field in ('model', 'asr_model'):
            if model_field not in values:
                continue
            model = str(values[model_field]).strip()
            if not model or len(model) > 200:
                raise ValueError("请填写模型名称")
            candidate[model_field] = model
        key = str(values.get("api_key") or "").strip()
        if key:
            import win32crypt
            candidate["protected_key"] = base64.b64encode(win32crypt.CryptProtectData(
                key.encode(), "NextLine API key", None, None, None, 0)).decode()
        if values.get("clear_api_key"):
            candidate.pop("protected_key", None)
        asr_key = str(values.get('asr_api_key') or '').strip()
        if asr_key:
            import win32crypt
            candidate['protected_asr_key'] = base64.b64encode(win32crypt.CryptProtectData(
                asr_key.encode(), 'HeartWeChat speech API', None, None, None, 0)).decode()
        if values.get('clear_asr_api_key'):
            candidate.pop('protected_asr_key', None)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        self.values = candidate
        return self.public()

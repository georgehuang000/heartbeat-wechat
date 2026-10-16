"""Counterpart appearance is independent of conversation tone and actual age."""
import json
import os
from pathlib import Path
from .pursuit import DEFAULT_PURSUIT_GOAL, PURSUIT_GOALS, PURSUIT_STAGES
from .scenes import SCENE_IDS, default_scene_id

ROLE_IDS = [f'{age}-{gender}' for age in ('child','teen','young','urban','mature','middle','older','senior')
            for gender in ('male','female')] + ['unknown-female', 'group-default']

def appearance_defaults(identity, profile=None):
    profile = profile or {}
    gender = profile.get('gender', 0)
    group = profile.get('conversation_kind') == 'group'
    return {'role': 'group-default' if group else 'young-female' if gender == 2 else
                    'young-male' if gender == 1 else 'unknown-female',
            'role_source': 'contact_gender' if not group and gender in (1, 2) else 'default',
            'scene_id': default_scene_id(identity)}

class ContactPreferences:
    def __init__(self, directory: Path):
        self.path = directory / 'contact-preferences.json'
        self.values = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def get(self, identity, profile=None):
        defaults=appearance_defaults(identity, profile)
        # No age is inferred from gender, name, avatar or message content.
        saved = dict(self.values.get(identity, {}))
        saved['assistance_enabled'] = saved.get('assistance_enabled') is True
        # Earlier explicit yes choices survive. An old default false is not a rejection.
        saved['assistance_decided'] = saved.get('assistance_decided') is True or saved['assistance_enabled']
        scene = saved.get('scene_id')
        saved['scene_id'] = scene if isinstance(scene, str) and scene in SCENE_IDS else default_scene_id(identity)
        goal = saved.get('pursuit_goal', DEFAULT_PURSUIT_GOAL)
        if not isinstance(goal, str) or goal not in PURSUIT_GOALS:
            goal = DEFAULT_PURSUIT_GOAL
        saved['pursuit_goal'] = goal
        stage = saved.get('pursuit_stage', 'unknown')
        saved['pursuit_stage'] = stage if isinstance(stage, str) and stage in PURSUIT_STAGES else 'unknown'
        if not isinstance(saved.get('adult_context', False), bool):
            saved['adult_context'] = False
        # Automatically saved defaults must follow refreshed profile data and
        # current default policy. Legacy explicit choices remain manual.
        if saved.get('role_source') in {'contact_gender', 'default'}:
            saved.pop('role', None)
            saved.pop('role_source', None)
        elif saved.get('role') in ROLE_IDS and not saved.get('role_source'):
            saved['role_source'] = 'manual'
        if (profile or {}).get('conversation_kind') == 'group':
            saved.update(adult_context=False, pursuit_stage='unknown')
        return {**defaults, 'adult_context': False, **saved}

    def save(self, identity, values, profile=None):
        old = self.get(identity,profile)
        if 'assistance_enabled' in values:
            if not isinstance(values['assistance_enabled'], bool):
                raise ValueError('联系人辅助开关必须为开启或关闭。')
            old['assistance_enabled'] = values['assistance_enabled']
            old['assistance_decided'] = True
        if 'reset_appearance' in values:
            if values['reset_appearance'] is not True or any(key in values for key in ('role', 'scene_id')):
                raise ValueError('形象重置请求无效')
            old.update(appearance_defaults(identity, profile))
        if 'scene_id' in values:
            scene = values['scene_id']
            if not isinstance(scene, str) or scene not in SCENE_IDS:
                raise ValueError('对话背景不存在，请选择书房、咖啡馆或公园。')
            old['scene_id'] = scene
        if 'role' in values:
            if values['role'] not in ROLE_IDS:
                raise ValueError('角色预设不存在')
            old['role'] = values['role']
            old['role_source'] = 'manual'
        if 'adult_context' in values:
            if not isinstance(values['adult_context'], bool):
                raise ValueError('成年关系场景配置无效')
            old['adult_context'] = values['adult_context']
        if 'pursuit_goal' in values:
            goal = values['pursuit_goal']
            if not isinstance(goal, str) or goal not in PURSUIT_GOALS:
                raise ValueError('聊天目标不存在，请选择先聊熟、表达兴趣或试着邀约。')
            old['pursuit_goal'] = goal
        if 'pursuit_stage' in values:
            stage = values['pursuit_stage']
            if not isinstance(stage, str) or stage not in PURSUIT_STAGES:
                raise ValueError('关系阶段不存在，请选择已确认的互动进展。')
            old['pursuit_stage'] = stage
        self.values[identity] = old
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, self.path)
        return old

import {useState} from 'react';
import {Shuffle} from '@phosphor-icons/react';
import {scenes, anotherSceneId} from './scenes';

export function SceneSettings({scene, contact, disabled, onChange}) {
  const [saving, setSaving] = useState(false);
  async function select(id) {
    if (disabled || saving || !contact) return;
    setSaving(true);
    try { await onChange({scene_id: id}); }
    finally { setSaving(false); }
  }
  return <section className="config-section scene-settings" aria-label="对话背景">
    <div className="section-top"><h3>对话背景</h3>
      <button type="button" className="text-button scene-shuffle" disabled={disabled || saving || !contact}
        onClick={() => select(anotherSceneId(scene.id))}><Shuffle size={16}/>换个背景</button>
    </div>
    <div className="scene-options">{scenes.map(option => <button type="button" key={option.id}
      aria-label={`使用${option.name}背景`} aria-pressed={scene.id === option.id}
      className={scene.id === option.id ? 'active' : ''} disabled={disabled || saving || !contact}
      onClick={() => select(option.id)}>
      <img src={option.image} alt="" loading="lazy"/><span>{option.name}</span>
    </button>)}</div>
    <p className="help">{contact ? `为${contact.name}保留这次搭配，换景时人物和草稿不变。` : '打开聊天后，可以为对方选择背景。'}</p>
  </section>;
}

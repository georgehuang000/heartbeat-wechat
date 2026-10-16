// These plates share rose/ivory light and a quiet text area, so they accompany
// every existing portrait without inferring the contact's actual age.
export const scenes = [
  {id: 'study', name: '书房', image: '/assets/scenes/study.png'},
  {id: 'cafe', name: '咖啡馆', image: '/assets/scenes/cafe.png'},
  {id: 'park', name: '公园', image: '/assets/scenes/park.png'},
];

export function defaultSceneId(identity) {
  let value = 2166136261;
  for (const byte of new TextEncoder().encode(String(identity))) {
    value = Math.imul(value ^ byte, 16777619) >>> 0;
  }
  return scenes[value % scenes.length].id;
}

export function resolveScene(person, identity) {
  return scenes.find(scene => scene.id === person?.scene_id)
    || scenes.find(scene => scene.id === defaultSceneId(identity));
}

export function anotherSceneId(current, random = Math.random) {
  const candidates = scenes.filter(scene => scene.id !== current);
  const index = Math.min(candidates.length - 1, Math.max(0, Math.floor(random() * candidates.length)));
  return candidates[index].id;
}

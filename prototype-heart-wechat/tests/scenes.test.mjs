import test from 'node:test';
import assert from 'node:assert/strict';
import {scenes, defaultSceneId, resolveScene, anotherSceneId} from '../src/scenes.js';

test('visual assignment stays stable through repeated renders and explicit overrides', () => {
  const identity = 'account:contact';
  const initial = defaultSceneId(identity);
  for (let i = 0; i < 100; i++) assert.equal(resolveScene({}, identity).id, initial);
  assert.equal(resolveScene({scene_id: 'cafe'}, identity).id, 'cafe');
  assert.equal(resolveScene({scene_id: '../external'}, identity).id, initial);
  assert.ok(scenes.every(scene => scene.image.startsWith('/assets/scenes/')));
});

test('an explicit shuffle cannot return the current backdrop', () => {
  for (const scene of scenes) {
    for (const random of [0, .49, .99, 1]) {
      const next = anotherSceneId(scene.id, () => random);
      assert.notEqual(next, scene.id);
      assert.ok(scenes.some(candidate => candidate.id === next));
    }
  }
});

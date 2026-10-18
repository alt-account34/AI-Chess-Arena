import test from 'node:test';
import assert from 'node:assert/strict';
import { timeline, positionAt, Playback } from '../demo/timeline.mjs';

test('compression uses median, bounds, fast book moves, and final turn', () => {
  const steps = timeline({ plies: [{ think_s: 0, book: true }, { think_s: 1 }, { think_s: 10 }, { think_s: 100 }], final_turn: { think_s: 10 } });
  assert.deepEqual(steps.map(s => s.duration), [0.3, 2, 3, 5, 3]);
  assert.equal(steps.at(-1).isMove, false);
  assert.equal(positionAt(steps, steps[2].start + 1.5).n, 2);
  assert.equal(positionAt(steps, steps[2].start + 1.5).progress, 0.5);
  assert.equal(positionAt(steps, steps.at(-1).end).n, 4);
});
test('missing timings and games without moves have defined behavior', () => {
  assert.equal(timeline({ plies: [{}] })[0].duration, 3);
  assert.deepEqual(positionAt([], 0), { n: 0, complete: [], active: null, progress: 1 });
  assert.equal(timeline({ plies: [], final_turn: {} })[0].duration, 3);
});
test('speed changes, pause, resume, seek and finish share one clock', () => {
  const player = new Playback(20);
  player.playing = true; player.advance(2);
  player.speed = 4; player.advance(2);
  assert.equal(player.time, 10);
  player.playing = false; player.advance(10);
  assert.equal(player.time, 10);
  player.seek(3); player.playing = true; player.advance(1);
  assert.equal(player.time, 7);
  player.advance(100);
  assert.equal(player.time, 20); assert.equal(player.playing, false);
  player.seek(-1); assert.equal(player.time, 0);
  player.seek(20 - 1e-12); assert.equal(player.time, 20);
});

// Pure playback model: one compressed clock for moves and recorded activity.
export function timeline(game) {
  const turns = [...game.plies, ...(game.final_turn ? [game.final_turn] : [])];
  const values = turns.filter(t => !t.book && t.think_s > 0).map(t => t.think_s).sort((a, b) => a - b);
  const mid = Math.floor(values.length / 2);
  const median = values.length ? (values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2) : 0;
  let end = 0;
  return turns.map((turn, index) => {
    const duration = turn.book ? 0.3 : median && turn.think_s > 0 ? Math.max(2, Math.min(5, 3 * turn.think_s / median)) : 3;
    const start = end;
    end += duration;
    return { turn, index, start, end, duration, isMove: index < game.plies.length };
  });
}

export function positionAt(steps, time) {
  const complete = steps.filter(s => s.end <= time);
  const active = steps.find(s => s.end > time) || null;
  return { n: complete.filter(s => s.isMove).length, complete, active,
    progress: active ? Math.max(0, Math.min(1, (time - active.start) / active.duration)) : 1 };
}

export class Playback {
  constructor(duration, speed = 1) { this.duration = duration; this.speed = speed; this.time = 0; this.playing = false; }
  advance(seconds) {
    if (this.playing) this.time = Math.min(this.duration, this.time + Math.max(0, seconds) * this.speed);
    if (this.time >= this.duration) this.playing = false;
  }
  seek(time) {
    // Range inputs can serialize their maximum with slightly less precision.
    this.time = time >= this.duration - 0.000001 ? this.duration : Math.max(0, time);
  }
}

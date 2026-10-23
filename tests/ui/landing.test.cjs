const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function setup(reduced = false) {
  const callbacks = {}, frames = new Map();
  let next = 0;
  const query = { matches: reduced, addEventListener: (_, fn) => (callbacks.motion ||= []).push(fn) };
  const button = { hidden: true, attributes: {}, addEventListener: (_, fn) => callbacks.click = fn,
    setAttribute(name, value) { this.attributes[name] = value; } };
  const ctx = new Proxy({}, { get: () => () => {} });
  const canvas = { getContext: () => ctx, getBoundingClientRect: () => ({ width: 700, height: 500 }) };
  const document = { hidden: false, documentElement: {},
    querySelector: selector => selector === '[data-sphere]' ? canvas : button,
    querySelectorAll: () => [], addEventListener: (name, fn) => callbacks[name] = fn };
  const window = { matchMedia: q => q.includes('reduced') ? query : { matches: true },
    addEventListener: (name, fn) => callbacks[name] = fn, devicePixelRatio: 1, IntersectionObserver: true };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../app/static/js/landing.js'), 'utf8'), {
    document, window, getComputedStyle: () => ({ getPropertyValue: () => '#4253e6' }),
    requestAnimationFrame: fn => { frames.set(++next, fn); return next; },
    cancelAnimationFrame: id => frames.delete(id),
    IntersectionObserver: class { constructor(fn) { callbacks.intersection = fn; } observe() {} },
  });
  return { callbacks, frames, button, document, query };
}

test('animation pauses offscreen, in hidden tabs, and by user choice', () => {
  const { callbacks, frames, button, document } = setup();
  callbacks.intersection([{ isIntersecting: true }]);
  assert.equal(frames.size, 1);
  callbacks.click();
  assert.equal(frames.size, 0);
  assert.equal(button.attributes['aria-pressed'], 'true');
  callbacks.intersection([{ isIntersecting: true }]);
  assert.equal(frames.size, 0);
  callbacks.click();
  assert.equal(frames.size, 1);
  document.hidden = true;
  callbacks.visibilitychange();
  assert.equal(frames.size, 0);
  document.hidden = false;
  callbacks.visibilitychange();
  assert.equal(frames.size, 1);
  callbacks.intersection([{ isIntersecting: false }]);
  assert.equal(frames.size, 0);
});

test('reduced motion is respected initially and when changed live', () => {
  const { callbacks, frames, button, query } = setup(true);
  callbacks.intersection([{ isIntersecting: true }]);
  assert.equal(frames.size, 0);
  assert.equal(button.hidden, true);
  query.matches = false;
  callbacks.motion.forEach(fn => fn());
  assert.equal(frames.size, 1);
  assert.equal(button.hidden, false);
  query.matches = true;
  callbacks.motion.forEach(fn => fn());
  assert.equal(frames.size, 0);
  assert.equal(button.hidden, true);
});

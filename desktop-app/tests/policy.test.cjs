const test = require('node:test');
const assert = require('node:assert/strict');
const { validateCommand, trustedFrame } = require('../electron/policy.cjs');
test('renderer cannot invoke arbitrary native operations', () => {
  for (const name of ['eval', 'exec', 'openExternal', 'shutdown', 'fs.readFile', 'team.import.files', 'team.export']) assert.throws(() => validateCommand(name, {}));
  assert.doesNotThrow(() => validateCommand('quota.arm', { enabled: false }));
});
test('oversized and non-object messages are refused', () => {
  for (const params of [null, [], 'bad']) assert.throws(() => validateCommand('status', params));
  assert.throws(() => validateCommand('quota.chat', {prompt: 'a'.repeat(110000)}));
});
test('only the application main frame can access the bridge', () => {
  const frame = {url: 'ailink://app/index.html'};
  const contents = {mainFrame: frame};
  const window = {webContents: contents};
  assert.equal(trustedFrame({sender: contents, senderFrame: frame}, window), true);
  assert.equal(trustedFrame({sender: contents, senderFrame: {url: 'https://example.com'}}, window), false);
  assert.equal(trustedFrame({sender: {}, senderFrame: frame}, window), false);
  frame.url='file:///bad.html'; assert.equal(trustedFrame({sender: contents,senderFrame:frame},window),false);
});

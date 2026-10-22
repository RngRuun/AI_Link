const methods = new Set(['status', 'network.enable', 'network.disable', 'network.configure', 'diagnostics.start',
  'diagnostics.cancel', 'kernel.apply', 'kernel.restore', 'quota.connect', 'quota.disconnect', 'quota.refresh',
  'quota.threads.browse','quota.threads.attach','quota.continuation','quota.prepare','quota.source.refresh','quota.source.configure','quota.configure', 'quota.arm', 'quota.select', 'quota.chat', 'quota.attribute', 'quota.profile.create', 'quota.profile.select',
  'storage.scan','storage.preview','storage.execute','storage.pin','storage.restore','storage.refresh',
  'network.preset','network.measure','network.measure.cancel','team.member.create','team.member.rename','team.assign','team.import.text','report']);
function validateCommand(method, params) {
  if (!methods.has(method) || !params || typeof params !== 'object' || Array.isArray(params)) throw new Error('不支持的桌面操作。');
  if (Buffer.byteLength(JSON.stringify(params), 'utf8') > 100000) throw new Error('操作参数过大。');
}
function trustedFrame(event, window) {
  return !!window && event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame
    && event.senderFrame.url.startsWith('ailink://app/');
}
module.exports = { validateCommand, trustedFrame };

import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/worker.js';
import { localEnv } from '../local.mjs';

async function get(env, path) {
  const response = await worker.fetch(new Request('http://127.0.0.1:8787/api' + path), env);
  return {status: response.status, body: await response.json()};
}
test('Mac release routes select architecture and never fall back to Windows', async () => {
  const env = localEnv(':memory:');
  env.HELPER_DOWNLOAD_URL = 'https://example.org/windows.zip';
  for (const route of ['/helper', '/v07/update']) {
    assert.equal((await get(env, route)).body.url, env.HELPER_DOWNLOAD_URL);
    const missing = await get(env, route + '?platform=macos&arch=arm64');
    assert.equal(missing.body.available, false);
    assert.equal(missing.body.url, null);
    for (const arch of ['arm64', 'x86_64']) {
      const meta = {version:'0.7.1',url:`https://github.com/linwu008/coursenest-releases/releases/download/v0.7.1/CourseNestHelper-0.7.1-macos-${arch}.zip`,sha256:'a'.repeat(64),signature:'test-signature'};
      env['HELPER_MACOS_' + arch.toUpperCase()] = JSON.stringify(meta);
      const release = await get(env, route + '?platform=macos&arch=' + arch);
      assert.equal(release.body.available, true);
      assert.equal(release.body.url, meta.url);
    }
    assert.equal((await get(env, route + '?platform=macos&arch=mips')).status, 400);
    env.HELPER_MACOS_ARM64 = env.HELPER_MACOS_X86_64;
    assert.equal((await get(env, route + '?platform=macos&arch=arm64')).body.available, false);
    delete env.HELPER_MACOS_ARM64;
    delete env.HELPER_MACOS_X86_64;
  }
});

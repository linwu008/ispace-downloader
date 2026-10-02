// Mac artifacts are opt-in; missing metadata must never select a Windows asset.
export function macRelease(url, env, check) {
  const platform = url.searchParams.get('platform');
  const arch = url.searchParams.get('arch');
  if (!platform || platform === 'windows') {
    check(!arch || arch === 'x86_64', '不支持的架构', 400);
    return null;
  }
  check(platform === 'macos' && ['arm64', 'x86_64'].includes(arch), '不支持的平台或架构', 400);
  let meta;
  try { meta = JSON.parse(env['HELPER_MACOS_' + arch.toUpperCase()] || 'null'); }
  catch { meta = null; }
  const filename = `CourseNestHelper-${meta?.version}-macos-${arch}.zip`;
  const expected = `https://github.com/linwu008/coursenest-releases/releases/download/v${meta?.version}/${filename}`;
  const available = !!(meta && /^\d+\.\d+\.\d+$/.test(meta.version) &&
    meta.url === expected && /^[a-f0-9]{64}$/.test(meta.sha256) && typeof meta.signature === 'string' && meta.signature.length);
  return { platform: 'macos', arch, available, version: available ? meta.version : null,
    url: available ? meta.url : null, sha256: available ? meta.sha256 : null,
    signature: available ? meta.signature : null, min_version: env.MIN_HELPER_VERSION || '0.4.0',
    reason: env.HELPER_UPDATE_REASON || '下载新版后退出助手并替换应用', site_version: '0.7.0' };
}

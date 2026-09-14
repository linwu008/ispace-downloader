export async function storageBudget(env, kind, units = 8) {
  const key = "r2:" + kind + ":" + new Date().toISOString().slice(0, 7);
  const maximum = Number(
    kind === "write"
      ? env.R2_WRITE_BUDGET || 800000
      : env.R2_READ_BUDGET || 8000000,
  );
  await env.DB.prepare("INSERT OR IGNORE INTO usage_counters VALUES (?,0)")
    .bind(key)
    .run();
  const reserved = await env.DB.prepare(
    "UPDATE usage_counters SET amount=amount+? WHERE key=? AND amount+?<=? RETURNING amount",
  )
    .bind(units, key, units, maximum)
    .first();
  if (!reserved)
    throw Object.assign(Error("本月存储操作额度已用完，请稍后重试"), {
      status: 429,
    });
}

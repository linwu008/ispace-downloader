import { advanceKnowledge } from "./knowledge.js";
import { createV05 } from "./v05.js";
const VERSION = "0.6.0";
const enc = new TextEncoder();
const hex = (b) =>
  Array.from(new Uint8Array(b), (n) => n.toString(16).padStart(2, "0")).join(
    "",
  );
const random = (n = 32) => hex(crypto.getRandomValues(new Uint8Array(n)));
const sha = async (s) =>
  hex(await crypto.subtle.digest("SHA-256", enc.encode(s)));
const json = (value, status = 200, headers = {}) =>
  Response.json(value, { status, headers });
const now = () => Math.floor(Date.now() / 1000);
function check(ok, message = "输入无效", status = 400) {
  if (!ok) throw Object.assign(new Error(message), { status });
}
function equal(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length)
    return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}
const first = (env, sql, ...args) =>
  env.DB.prepare(sql)
    .bind(...args)
    .first();
const all = async (env, sql, ...args) =>
  (
    await env.DB.prepare(sql)
      .bind(...args)
      .all()
  ).results;
const run = (env, sql, ...args) =>
  env.DB.prepare(sql)
    .bind(...args)
    .run();
const text = (value, max = 200) =>
  typeof value === "string" ? value.slice(0, max) : "";
const integer = (x) => Number.isSafeInteger(x) && x > 0;
async function passwordHash(password, salt) {
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  return hex(
    await crypto.subtle.deriveBits(
      {
        name: "PBKDF2",
        salt: enc.encode(salt),
        iterations: 100000,
        hash: "SHA-256",
      },
      key,
      256,
    ),
  );
}
async function throttle(env, key, limit, seconds = 600) {
  const bucket = `${key}:${Math.floor(now() / seconds)}`;
  await run(
    env,
    "INSERT INTO throttles VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET hits=hits+1",
    bucket,
    now() + seconds,
  );
  check(
    (await first(env, "SELECT hits FROM throttles WHERE key=?", bucket)).hits <=
      limit,
    "请求过于频繁，请稍后再试",
    429,
  );
}
async function body(request) {
  check(
    (request.headers.get("content-type") || "").includes("application/json"),
    "需要 JSON 请求",
  );
  check(
    Number(request.headers.get("content-length") || 0) <= 2_000_000,
    "请求过大",
    413,
  );
  const data = await request.text();
  check(data.length <= 2_000_000, "请求过大", 413);
  try {
    const value = JSON.parse(data);
    check(value && typeof value === "object" && !Array.isArray(value));
    return value;
  } catch {
    throw Object.assign(new Error("JSON 内容无效"), { status: 400 });
  }
}
function cookie(token, env, maxAge = 604800) {
  return `cn_session=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${env.LOCAL_DEV === "1" ? "" : "; Secure"}`;
}
async function session(request, env, write = false) {
  const raw = (request.headers.get("cookie") || "").match(
    /(?:^|;\s*)cn_session=([a-f0-9]{64})(?:;|$)/,
  )?.[1];
  check(raw, "请先登录", 401);
  const row = await first(
    env,
    "SELECT u.id,u.email FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?",
    await sha(raw),
    now(),
  );
  check(row, "登录已过期，请重新登录", 401);
  const csrf = await sha("csrf:" + raw);
  if (write)
    check(
      equal(request.headers.get("x-csrf-token"), csrf),
      "页面已过期，请刷新",
      403,
    );
  return { ...row, csrf };
}
async function deviceAuth(request, env) {
  const raw = (request.headers.get("authorization") || "").match(
    /^Bearer ([a-f0-9]{64})$/,
  )?.[1];
  check(raw, "设备未配对", 401);
  const device = await first(
    env,
    "SELECT * FROM devices WHERE token=? AND revoked=0",
    await sha(raw),
  );
  check(device, "设备授权已撤销，请重新配对", 401);
  return device;
}
function publicDevice(d) {
  if (!d) return null;
  return {
    id: d.id,
    name: d.name,
    online: now() - d.last_seen < 75,
    last_seen: d.last_seen,
    snapshot: JSON.parse(d.snapshot),
    schedule: { enabled: !!d.schedule_enabled, time: d.schedule_time },
  };
}
function sanitizeSnapshot(b) {
  check(
    Array.isArray(b.courses) &&
      b.courses.length <= 500 &&
      Array.isArray(b.groups) &&
      b.groups.length <= 3000 &&
      Array.isArray(b.materials) &&
      b.materials.length <= 10000,
    "设备资料清单过大或无效",
  );
  const courses = b.courses.map((c) => {
    check(integer(c.id));
    return {
      id: c.id,
      name: text(c.name),
      membership: ["added", "available", "removed"].includes(c.membership)
        ? c.membership
        : "available",
      sync_mode: c.sync_mode === "all" ? "all" : "selected",
      bound: !!c.bound,
      enabled: !!c.enabled,
      folder: text(c.folder, 600),
    };
  });
  const ids = new Set(courses.map((c) => c.id));
  const groups = b.groups.map((g) => {
    check(ids.has(g.course_id) && typeof g.id === "string");
    return {
      id: text(g.id, 64),
      course_id: g.course_id,
      title: text(g.title),
      folder: text(g.folder, 300),
      position: Number(g.position) || 0,
    };
  });
  const groupIds = new Map(groups.map((g) => [g.id, g.course_id]));
  const materials = b.materials.map((m) => {
    check(
      integer(m.id) &&
        ids.has(m.course_id) &&
        groupIds.get(m.group_id) === m.course_id,
    );
    return {
      id: m.id,
      course_id: m.course_id,
      group_id: text(m.group_id, 64),
      name: text(m.name),
      selected: !!m.selected,
      status: text(m.status, 40),
      path: text(m.path, 800),
      error: text(m.error, 300),
    };
  });
  return {
    version: text(b.version, 30),
    capabilities: Array.isArray(b.capabilities)
      ? b.capabilities.filter((x) => ["archive-v1", "setup-v1", "cancel-v1", "schedule-v1"].includes(x))
      : [],
    courses,
    groups,
    materials,
    auth: text(b.auth, 40),
    paused: !!b.paused,
    local_schedule: !!b.local_schedule,
    plan_migration: {pending:!!b.plan_migration?.pending, enabled:!!b.plan_migration?.enabled, time:/^([01]\d|2[0-3]):[0-5]\d$/.test(b.plan_migration?.time) ? b.plan_migration.time : "20:00"},
    at: now(),
    truncated: !!b.truncated,
  };
}
function validateCommand(kind, p, snapshot) {
  check(p && typeof p === "object");
  const courses = snapshot.courses || [],
    materials = snapshot.materials || [];
  const course = (id) => {
    check(
      integer(id) && courses.some((c) => c.id === id),
      "课程不属于此设备或清单尚未更新",
    );
    return courses.find((c) => c.id === id);
  };
  if (kind === "refresh_courses") return {};
  if (kind === "add_courses" || kind === "remove_courses") {
    check(Array.isArray(p.ids) && p.ids.length > 0 && p.ids.length <= 100);
    p.ids.forEach(course);
    return { ids: [...new Set(p.ids)] };
  }
  if (kind === "catalog") {
    course(p.course_id);
    return { course_id: p.course_id };
  }
  if (kind === "selection") {
    check(course(p.course_id).membership === "added", "请先添加课程");
    check(
      ["all", "selected"].includes(p.mode) &&
        Array.isArray(p.ids) &&
        p.ids.length <= 10000,
    );
    check(
      p.ids.every(
        (id) =>
          integer(id) &&
          materials.some((m) => m.id === id && m.course_id === p.course_id),
      ),
      "文件不属于此课程",
    );
    return { course_id: p.course_id, mode: p.mode, ids: [...new Set(p.ids)] };
  }
  if (kind === "sync") {
    if (p.course_id != null) {
      const c = course(p.course_id);
      check(
        c.bound && c.membership === "added",
        "请先在同步助手中为课程授权本地目录",
      );
      return { course_id: p.course_id };
    }
    check(
      courses.some((c) => c.bound && c.enabled && c.membership === "added"),
      "没有已授权目录并启用同步的课程",
    );
    return {};
  }
  throw Object.assign(new Error("不支持此任务"), { status: 400 });
}
async function enqueue(env, d, kind, p, key) {
  const existing = await first(
    env,
    "SELECT id FROM jobs WHERE request_key=?",
    key,
  );
  if (existing) return existing.id;
  check(
    (
      await first(
        env,
        "SELECT COUNT(*) AS n FROM jobs WHERE device_id=? AND status IN ('queued','running','canceling')",
        d.id,
      )
    ).n < 30,
    "等待中的任务过多，请稍后再试",
    429,
  );
  const id = crypto.randomUUID();
  await run(
    env,
    "INSERT OR IGNORE INTO jobs(id,user_id,device_id,kind,payload,created,updated,request_key) VALUES (?,?,?,?,?,?,?,?)",
    id,
    d.user_id,
    d.id,
    kind,
    JSON.stringify(p),
    now(),
    now(),
    key,
  );
  return (await first(env, "SELECT id FROM jobs WHERE request_key=?", key)).id;
}
async function due(env, d) {
  const snapshot = JSON.parse(d.snapshot);
  if (!d.schedule_enabled || snapshot.local_schedule || snapshot.plan_migration?.pending || snapshot.paused) return;
  const local = new Date((now() + 8 * 3600) * 1000).toISOString(),
    day = local.slice(0, 10),
    time = local.slice(11, 16);
  if (time < d.schedule_time || d.last_day === day) return;
  if (
    !(snapshot.courses || []).some(
      (c) => c.bound && c.enabled && c.membership === "added",
    )
  )
    return;
  await run(
    env,
    "UPDATE jobs SET status='canceled',result=? WHERE device_id=? AND status='queued' AND request_key LIKE 'daily:%' AND request_key<>?",
    JSON.stringify({ message: "离线期间的重复每日检查已合并" }),
    d.id,
    `daily:${d.id}:${day}`,
  );
  await enqueue(env, d, "sync", {}, `daily:${d.id}:${day}`);
  await run(env, "UPDATE devices SET last_day=? WHERE id=?", day, d.id);
}
const v05 = createV05({
  first,
  all,
  run,
  body,
  json,
  check,
  random,
  sha,
  now,
  session,
  deviceAuth,
  throttle,
  passwordHash,
});
async function api(request, env, url) {
  const added = await v05.route(request, env, url);
  if (added) return added;
  const path = url.pathname,
    method = request.method;
  if (path === "/api/health" && method === "GET")
    return json({
      version: VERSION,
      name: "BNBU CourseNest",
      local: env.LOCAL_DEV === "1",
      registration: env.INVITE_REQUIRED === "0" ? "open" : "invite",
    });
  const ip = request.headers.get("cf-connecting-ip") || "local";
  if (
    ["/api/auth/register", "/api/auth/login"].includes(path) &&
    method === "POST"
  ) {
    await throttle(env, "auth:" + ip, 30);
    const b = await body(request),
      email = text(b.email, 254).trim().toLowerCase();
    check(/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email), "请输入有效邮箱");
    check(
      typeof b.password === "string" &&
        b.password.length >= 8 &&
        b.password.length <= 128,
      "密码需要 8–128 个字符",
    );
    let user = await first(env, "SELECT * FROM users WHERE email=?", email);
    if (path.endsWith("register")) {
      check(
        env.INVITE_REQUIRED === "0" ||
          (env.INVITE_CODE && equal(text(b.invite, 200), env.INVITE_CODE)),
        "邀请码无效；首版仅开放邀请体验",
        403,
      );
      check(!user, "该邮箱已注册，请登录", 409);
      const id = crypto.randomUUID(),
        salt = random(16),
        password = await passwordHash(b.password, salt);
      await run(
        env,
        "INSERT INTO users VALUES (?,?,?,?,?)",
        id,
        email,
        password,
        salt,
        now(),
      );
      user = { id, email };
      if (env.MAILER || (env.RESEND_API_KEY && env.MAIL_FROM)) {
        try {
          await v05.send(env, user, "verify");
        } catch {
          /* Account remains usable; verification can be requested again. */
        }
      }
    } else {
      const candidate = await passwordHash(
        b.password,
        user?.salt || "invalid-account-salt",
      );
      check(user && equal(candidate, user.password), "邮箱或密码不正确", 401);
    }
    const raw = random();
    await run(
      env,
      "INSERT INTO sessions VALUES (?,?,?)",
      await sha(raw),
      user.id,
      now() + 604800,
    );
    return json({ ok: true }, 200, { "Set-Cookie": cookie(raw, env) });
  }
  if (path === "/api/device/pair" && method === "POST") {
    await throttle(env, "pair:" + ip, 10);
    const b = await body(request),
      code = await sha(
        text(b.code, 30).replaceAll("-", "").trim().toUpperCase(),
      ),
      name = text(b.name, 80).trim();
    check(name, "请输入设备名称");
    const id = crypto.randomUUID(),
      raw = random(),
      token = await sha(raw);
    try {
      const row = await first(
        env,
        "INSERT INTO devices(id,user_id,token,name) SELECT ?,user_id,?,? FROM pairings WHERE code=? AND used=0 AND expires>? RETURNING id,user_id",
        id,
        token,
        name,
        code,
        now(),
      );
      check(row, "配对码无效或已过期");
      await run(env, "UPDATE pairings SET used=1 WHERE user_id=?", row.user_id);
      return json({ device_id: id, token: raw });
    } catch (e) {
      if (e.status) throw e;
      throw Object.assign(
        new Error("该账号已有配对设备，请先在网站解除旧设备"),
        { status: 409 },
      );
    }
  }
  if (path.startsWith("/api/device/") && method === "POST") {
    const d = await deviceAuth(request, env),
      b = await body(request);
    if (path === "/api/device/poll") {
      if (b.snapshot) {
        const snapshot = sanitizeSnapshot(b.snapshot);
        await run(
          env,
          "UPDATE devices SET snapshot=?,last_seen=? WHERE id=?",
          JSON.stringify(snapshot),
          now(),
          d.id,
        );
        d.snapshot = JSON.stringify(snapshot);
      } else
        await run(
          env,
          "UPDATE devices SET last_seen=? WHERE id=?",
          now(),
          d.id,
        );
      const migration = JSON.parse(d.snapshot).plan_migration;
      const conflict = !!(migration?.pending && migration.enabled && d.schedule_enabled && migration.time !== d.schedule_time);
      if (migration?.pending && migration.enabled && !conflict && !d.schedule_enabled) {
        await run(env, "UPDATE devices SET schedule_enabled=1,schedule_time=? WHERE id=?",migration.time,d.id);
        d.schedule_enabled=1; d.schedule_time=migration.time;
      }
      const plan = {enabled:!!d.schedule_enabled, time:d.schedule_time, conflict};
      try {
        await due(env, d);
      } catch {
        // A full queue must still drain; the next poll retries the daily enqueue.
      }
      await run(
        env,
        "UPDATE jobs SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,result=CASE WHEN attempts>=3 THEN ? ELSE result END WHERE device_id=? AND status='running' AND lease_until<?",
        JSON.stringify({ message: "任务多次中断，请手动重试" }),
        d.id,
        now(),
      );
      const cancellations = await all(env, "SELECT id,lease_token FROM jobs WHERE device_id=? AND status='canceling'", d.id);
      if (b.paused || JSON.parse(d.snapshot).paused) return json({ job: null, cancellations, plan });
      const lease = random(16);
      const job = await first(
        env,
        "UPDATE jobs SET status='running',attempts=attempts+1,lease_until=?,lease_token=?,updated=? WHERE id=(SELECT id FROM jobs WHERE device_id=? AND status='queued' ORDER BY created,rowid LIMIT 1) AND NOT EXISTS (SELECT 1 FROM jobs WHERE device_id=? AND status IN ('running','canceling')) RETURNING id,kind,payload,lease_token",
        now() + 120,
        lease,
        now(),
        d.id,
        d.id,
      );
      return json({
        cancellations, plan,
        job: job ? { ...job, payload: JSON.parse(job.payload) } : null,
      });
    }
    const match = path.match(
      /^\/api\/device\/jobs\/([\w-]+)\/(heartbeat|complete)$/,
    );
    check(match, "接口不存在", 404);
    const job = await first(
      env,
      "SELECT * FROM jobs WHERE id=? AND device_id=?",
      match[1],
      d.id,
    );
    check(job, "任务不存在", 404);
    check(
      equal(b.lease_token, job.lease_token),
      "任务已重新分配，请重新读取",
      409,
    );
    if (match[2] === "heartbeat") {
      check(["running", "canceling"].includes(job.status), "任务已结束", 409);
      await run(
        env,
        "UPDATE jobs SET lease_until=? WHERE id=?",
        now() + 120,
        job.id,
      );
      await run(env, "UPDATE devices SET last_seen=? WHERE id=?", now(), d.id);
      return json({ ok: true, cancel_requested: job.status === "canceling" });
    }
    check(
      ["success", "partial", "failed", "auth_required", "canceled"].includes(b.status),
      "任务状态无效",
    );
    const result = {
      message: text(b.result?.message, 600),
      downloaded: Number(b.result?.downloaded) || 0,
      skipped: Number(b.result?.skipped) || 0,
      failed: Number(b.result?.failed) || 0,
    };
    await run(
      env,
      "UPDATE jobs SET status=?,result=?,updated=? WHERE id=? AND status IN ('running','canceling')",
      b.status,
      JSON.stringify(result),
      now(),
      job.id,
    );
    return json({ ok: true });
  }
  const user = await session(request, env, !["GET", "HEAD"].includes(method));
  const d = await first(
    env,
    "SELECT * FROM devices WHERE user_id=? AND revoked=0",
    user.id,
  );
  if (path === "/api/me" && method === "GET")
    return json({
      user: { id: user.id, email: user.email },
      csrf: user.csrf,
      device: publicDevice(d),
      version: VERSION,
    });
  if (path === "/api/auth/logout" && method === "POST") {
    const raw = (request.headers.get("cookie") || "").match(
      /cn_session=([a-f0-9]{64})/,
    )?.[1];
    await run(env, "DELETE FROM sessions WHERE token=?", await sha(raw));
    return json({ ok: true }, 200, { "Set-Cookie": cookie("", env, 0) });
  }
  if (path === "/api/pairings" && method === "POST") {
    check(!d, "请先解除当前设备", 409);
    await throttle(env, "pair-create:" + user.id, 10);
    const code = random(6).toUpperCase();
    await run(env, "UPDATE pairings SET used=1 WHERE user_id=?", user.id);
    await run(
      env,
      "INSERT INTO pairings VALUES (?,?,?,0)",
      await sha(code),
      user.id,
      now() + 600,
    );
    return json({ code: code.match(/.{4}/g).join("-"), expires: now() + 600 });
  }
  if (path === "/api/device" && method === "DELETE") {
    if (d)
      await env.DB.batch([
        env.DB.prepare(
          "UPDATE devices SET revoked=1,snapshot='{}' WHERE id=?",
        ).bind(d.id),
        env.DB.prepare(
          "UPDATE jobs SET status='canceled' WHERE device_id=? AND status IN ('queued','running','canceling')",
        ).bind(d.id),
      ]);
    await run(env, "UPDATE pairings SET used=1 WHERE user_id=?", user.id);
    return json({ ok: true });
  }
  const cancelMatch = path.match(/^\/api\/jobs\/([\w-]+)\/cancel$/);
  if (cancelMatch && method === "POST") {
    const job = await first(env, "SELECT * FROM jobs WHERE id=? AND user_id=?", cancelMatch[1], user.id);
    check(job, "任务不存在", 404);
    const canInterrupt = JSON.parse(d?.snapshot || "{}").capabilities?.includes("cancel-v1");
    if (job.status === "running") check(canInterrupt, "请先升级电脑助手至 v0.6 后取消运行中任务", 409);
    await run(env, "UPDATE jobs SET status=CASE WHEN status='queued' THEN 'canceled' ELSE 'canceling' END,result=?,updated=? WHERE id=? AND user_id=? AND (status='queued' OR (status='running' AND ?=1))",
      JSON.stringify({message: job.status === "queued" ? "待执行任务已取消" : "取消请求已提交，等待电脑确认停止"}),now(),job.id,user.id,canInterrupt ? 1 : 0);
    return json({ok:true});
  }
  if (path === "/api/jobs" && method === "GET") {
    const jobs = await all(
      env,
      "SELECT id,kind,payload,status,created,updated,result FROM jobs WHERE user_id=? ORDER BY created DESC,id DESC LIMIT 100",
      user.id,
    );
    return json({
      items: jobs.map((j) => ({
        ...j,
        payload: JSON.parse(j.payload),
        result: JSON.parse(j.result),
      })),
    });
  }
  if (path === "/api/jobs" && method === "POST") {
    check(d, "请先配对电脑");
    await throttle(env, "job:" + user.id, 60);
    const b = await body(request),
      p = validateCommand(b.kind, b.payload || {}, JSON.parse(d.snapshot));
    check(
      typeof b.request_id === "string" && /^[\w-]{16,80}$/.test(b.request_id),
      "任务标识无效",
    );
    return json(
      { id: await enqueue(env, d, b.kind, p, `${user.id}:${b.request_id}`) },
      202,
    );
  }
  if (path === "/api/schedule" && method === "PUT") {
    check(d, "请先配对电脑");
    const b = await body(request);
    check(
      typeof b.enabled === "boolean" &&
        /^([01]\d|2[0-3]):[0-5]\d$/.test(b.time),
      "时间无效",
    );
    check(
      !b.enabled || !JSON.parse(d.snapshot).local_schedule,
      "请先在本地助手关闭原每日任务，再启用网站计划",
    );
    if (JSON.parse(d.snapshot).plan_migration?.pending) {
      check(JSON.parse(d.snapshot).capabilities?.includes("schedule-v1"), "请先升级助手", 409);
      await enqueue(env,d,"schedule_resolve",{enabled:b.enabled,time:b.time},`schedule:${d.id}:${random(8)}`);
    }
    await run(
      env,
      "UPDATE devices SET schedule_enabled=?,schedule_time=? WHERE id=?",
      b.enabled ? 1 : 0,
      b.time,
      d.id,
    );
    return json({ ok: true });
  }
  throw Object.assign(new Error("接口不存在"), { status: 404 });
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    let response;
    try {
      check(url.origin === env.PUBLIC_ORIGIN, "网站地址未获授权", 403);
      const origin = request.headers.get("origin");
      check(!origin || origin === env.PUBLIC_ORIGIN, "拒绝跨站请求", 403);
      check(
        request.headers.get("sec-fetch-site") !== "cross-site" ||
          request.method === "GET",
        "拒绝跨站请求",
        403,
      );
      response = url.pathname.startsWith("/api/")
        ? await api(request, env, url)
        : await env.ASSETS.fetch(request);
    } catch (error) {
      response = json(
        { detail: error.status ? error.message : "服务暂时不可用，请稍后重试" },
        error.status || 500,
      );
    }
    const headers = new Headers(response.headers);
    headers.set("Cache-Control", "no-store");
    headers.set("X-Content-Type-Options", "nosniff");
    headers.set("Referrer-Policy", "no-referrer");
    headers.set(
      "Content-Security-Policy",
      "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    );
    return new Response(response.body, { status: response.status, headers });
  },
  async scheduled(_event, env) {
    try {
      if (env.ARCHIVE_ENABLED !== "0" && env.ARCHIVE_BUCKET) await advanceKnowledge(env);
    } catch {
      /* Archive budgets do not block existing device schedules. */
    }
    if (env.ARCHIVE_ENABLED !== "0" && env.ARCHIVE_BUCKET) {
      const expired = await all(
        env,
        "SELECT id FROM uploads WHERE status IN ('pending','expired') AND expires<? LIMIT 50",
        now(),
      );
      for (const u of expired) {
        await env.ARCHIVE_BUCKET.delete("uploads/" + u.id);
        await env.ARCHIVE_BUCKET.delete("text/" + u.id);
        await run(
          env,
          "UPDATE uploads SET status='cleaned' WHERE id=? AND status IN ('pending','expired')",
          u.id,
        );
      }
    }
    if (env.MAIL_ENABLED !== "0" && (env.MAILER || (env.RESEND_API_KEY && env.MAIL_FROM))) await run(env, "DELETE FROM mail_tokens WHERE expires<?", now());
    const devices = await all(
      env,
      "SELECT * FROM devices WHERE revoked=0 AND schedule_enabled=1",
    );
    for (const d of devices) {
      try {
        await due(env, d);
      } catch {
        /* Next poll retries without losing a day. */
      }
    }
    await run(env, "DELETE FROM sessions WHERE expires<?", now());
    await run(env, "DELETE FROM pairings WHERE expires<?", now());
    await run(env, "DELETE FROM throttles WHERE expires<?", now());
  },
};

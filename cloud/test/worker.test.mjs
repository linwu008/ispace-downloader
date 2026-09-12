import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/worker.js";
import { localEnv } from "../local.mjs";

const origin = "http://127.0.0.1:8787";
async function request(env, path, method = "GET", body, auth = {}, extra = {}) {
  const headers = {
    Origin: origin,
    ...(body ? { "Content-Type": "application/json" } : {}),
    ...(auth.cookie ? { Cookie: auth.cookie } : {}),
    ...(auth.csrf ? { "X-CSRF-Token": auth.csrf } : {}),
    ...(auth.token ? { Authorization: "Bearer " + auth.token } : {}),
    ...extra,
  };
  const res = await worker.fetch(
    new Request(origin + "/api" + path, {
      method,
      headers,
      ...(body ? { body: JSON.stringify(body) } : {}),
    }),
    env,
  );
  return {
    status: res.status,
    body: await res.json(),
    cookie: res.headers.get("set-cookie")?.split(";")[0],
  };
}
async function user(env, email = "one@example.test") {
  const r = await request(env, "/auth/register", "POST", {
    email,
    password: "long-password-123!",
    invite: "NEST-LOCAL-04",
  });
  assert.equal(r.status, 200);
  const auth = { cookie: r.cookie };
  auth.csrf = (await request(env, "/me", "GET", null, auth)).body.csrf;
  return auth;
}
async function pair(env, auth) {
  const r = await request(env, "/pairings", "POST", null, auth);
  assert.equal(r.status, 200);
  const d = await request(env, "/device/pair", "POST", {
    code: r.body.code,
    name: "Test PC",
  });
  assert.equal(d.status, 200);
  return { ...d.body, code: r.body.code };
}
const snapshot = () => ({
  courses: [
    {
      id: 1,
      name: "Computing",
      membership: "added",
      bound: true,
      enabled: true,
      sync_mode: "selected",
      folder: "F:\\Courses",
    },
  ],
  groups: [
    {
      id: "week1",
      course_id: 1,
      title: "Week 1",
      folder: "01 Week 1",
      position: 1000,
    },
  ],
  materials: [
    {
      id: 1,
      course_id: 1,
      group_id: "week1",
      name: "notes.txt",
      selected: false,
      status: "pending",
      url: "https://secret-school-token.test",
      password: "secret",
    },
  ],
  auth: "logged_in",
  local_schedule: false,
});
async function seed(env) {
  const auth = await user(env),
    d = await pair(env, auth);
  await request(
    env,
    "/device/poll",
    "POST",
    { snapshot: snapshot(), paused: true },
    d,
  );
  return { auth, d };
}
const enqueue = (
  env,
  auth,
  kind = "sync",
  payload = {},
  id = crypto.randomUUID(),
) => request(env, "/jobs", "POST", { kind, payload, request_id: id }, auth);

test("registration requires invitation; passwords are hashed, sessions protected", async () => {
  const env = localEnv();
  try {
    assert.equal(
      (
        await request(env, "/auth/register", "POST", {
          email: "a@b.test",
          password: "long-password-123!",
          invite: "bad",
        })
      ).status,
      403,
    );
    const auth = await user(env);
    const row = env.raw.prepare("SELECT * FROM users").get();
    assert.notEqual(row.password, "long-password-123!");
    assert.equal(row.password.length, 64);
    assert.equal(
      (await request(env, "/pairings", "POST", null, { cookie: auth.cookie }))
        .status,
      403,
    );
    assert.equal(
      (
        await request(env, "/pairings", "POST", null, auth, {
          Origin: "https://evil.test",
        })
      ).status,
      403,
    );
    await request(env, "/auth/logout", "POST", null, auth);
    assert.equal((await request(env, "/me", "GET", null, auth)).status, 401);
  } finally {
    env.close();
  }
});
test("login rejects wrong password and returns correct account", async () => {
  const env = localEnv();
  try {
    await user(env);
    assert.equal(
      (
        await request(env, "/auth/login", "POST", {
          email: "one@example.test",
          password: "wrong-password-123",
        })
      ).status,
      401,
    );
    assert.equal(
      (
        await request(env, "/auth/login", "POST", {
          email: "one@example.test",
          password: "long-password-123!",
        })
      ).status,
      200,
    );
  } finally {
    env.close();
  }
});
test("one-use pairing, one active device, expiration and revocation", async () => {
  const env = localEnv();
  try {
    const auth = await user(env),
      d = await pair(env, auth);
    assert.equal(
      (
        await request(env, "/device/pair", "POST", {
          code: d.code,
          name: "Other",
        })
      ).status,
      400,
    );
    assert.equal(
      (await request(env, "/pairings", "POST", null, auth)).status,
      409,
    );
    await request(env, "/device", "DELETE", null, auth);
    assert.equal(
      (await request(env, "/device/poll", "POST", {}, d)).status,
      401,
    );
    const p = await request(env, "/pairings", "POST", null, auth);
    env.raw.exec("UPDATE pairings SET expires=0");
    assert.equal(
      (
        await request(env, "/device/pair", "POST", {
          code: p.body.code,
          name: "Other",
        })
      ).status,
      400,
    );
  } finally {
    env.close();
  }
});
test("two accounts cannot see or claim each others device tasks", async () => {
  const env = localEnv();
  try {
    const { auth, d } = await seed(env),
      other = await user(env, "two@example.test"),
      d2 = await pair(env, other);
    await enqueue(env, auth);
    assert.equal(
      (await request(env, "/jobs", "GET", null, other)).body.items.length,
      0,
    );
    assert.equal(
      (await request(env, "/me", "GET", null, other)).body.device.name,
      "Test PC",
    );
    assert.equal(
      (await request(env, "/device/poll", "POST", {}, d2)).body.job,
      null,
    );
    const job = (await request(env, "/device/poll", "POST", {}, d)).body.job;
    assert.ok(job);
    assert.equal(
      (
        await request(
          env,
          `/device/jobs/${job.id}/complete`,
          "POST",
          { lease_token: job.lease_token, status: "success", result: {} },
          d2,
        )
      ).status,
      404,
    );
  } finally {
    env.close();
  }
});
test("snapshots exclude school URLs, cookies and passwords", async () => {
  const env = localEnv();
  try {
    const { auth } = await seed(env),
      state = (await request(env, "/me", "GET", null, auth)).body;
    const value = JSON.stringify(state);
    assert.ok(!value.includes("secret-school"));
    assert.ok(!value.includes("password"));
    assert.equal(state.device.snapshot.materials[0].name, "notes.txt");
  } finally {
    env.close();
  }
});
test("job enqueue is idempotent and executes selection before sync", async () => {
  const env = localEnv();
  try {
    const { auth, d } = await seed(env),
      key = crypto.randomUUID();
    const a = await enqueue(
      env,
      auth,
      "selection",
      { course_id: 1, ids: [1], mode: "selected" },
      key,
    );
    assert.equal(
      (
        await enqueue(
          env,
          auth,
          "selection",
          { course_id: 1, ids: [1], mode: "selected" },
          key,
        )
      ).body.id,
      a.body.id,
    );
    await enqueue(env, auth);
    const job = (await request(env, "/device/poll", "POST", {}, d)).body.job;
    assert.equal(job.kind, "selection");
    assert.equal(
      (await request(env, "/device/poll", "POST", {}, d)).body.job,
      null,
    );
    await request(
      env,
      `/device/jobs/${job.id}/complete`,
      "POST",
      {
        lease_token: job.lease_token,
        status: "success",
        result: { message: "ok" },
      },
      d,
    );
    assert.equal(
      (await request(env, "/device/poll", "POST", {}, d)).body.job.kind,
      "sync",
    );
  } finally {
    env.close();
  }
});
test("unknown commands and foreign material selections are rejected", async () => {
  const env = localEnv();
  try {
    const { auth } = await seed(env);
    assert.equal(
      (await enqueue(env, auth, "shell", { command: "whoami" })).status,
      400,
    );
    assert.equal(
      (
        await enqueue(env, auth, "selection", {
          course_id: 1,
          ids: [99],
          mode: "selected",
        })
      ).status,
      400,
    );
    assert.equal(
      (await enqueue(env, auth, "sync", { course_id: 999 })).status,
      400,
    );
  } finally {
    env.close();
  }
});
test("paused device keeps queue; heartbeat and stale lease validation", async () => {
  const env = localEnv();
  try {
    const { auth, d } = await seed(env);
    await enqueue(env, auth);
    assert.equal(
      (await request(env, "/device/poll", "POST", { paused: true }, d)).body
        .job,
      null,
    );
    const job = (await request(env, "/device/poll", "POST", {}, d)).body.job;
    assert.equal(
      (
        await request(
          env,
          `/device/jobs/${job.id}/heartbeat`,
          "POST",
          { lease_token: job.lease_token },
          d,
        )
      ).status,
      200,
    );
    env.raw.exec("UPDATE jobs SET lease_until=0");
    const retry = (await request(env, "/device/poll", "POST", {}, d)).body.job;
    assert.equal(retry.id, job.id);
    assert.notEqual(retry.lease_token, job.lease_token);
    assert.equal(
      (
        await request(
          env,
          `/device/jobs/${job.id}/complete`,
          "POST",
          { lease_token: job.lease_token, status: "success" },
          d,
        )
      ).status,
      409,
    );
  } finally {
    env.close();
  }
});
test("three expired leases become failure instead of infinite retries", async () => {
  const env = localEnv();
  try {
    const { auth, d } = await seed(env);
    await enqueue(env, auth);
    for (let i = 0; i < 3; i++) {
      assert.ok((await request(env, "/device/poll", "POST", {}, d)).body.job);
      env.raw.exec("UPDATE jobs SET lease_until=0");
    }
    assert.equal(
      (await request(env, "/device/poll", "POST", {}, d)).body.job,
      null,
    );
    assert.equal(
      (await request(env, "/jobs", "GET", null, auth)).body.items[0].status,
      "failed",
    );
  } finally {
    env.close();
  }
});
test("daily missed checks coalesce and local scheduler conflict is blocked", async () => {
  const env = localEnv();
  try {
    const { auth, d } = await seed(env);
    assert.equal(
      (
        await request(
          env,
          "/schedule",
          "PUT",
          { enabled: true, time: "00:00" },
          auth,
        )
      ).status,
      200,
    );
    await worker.scheduled({}, env);
    await worker.scheduled({}, env);
    assert.equal(
      (await request(env, "/jobs", "GET", null, auth)).body.items.length,
      1,
    );
    env.raw.exec(
      "UPDATE devices SET last_day='2000-01-01'; UPDATE jobs SET request_key='daily:old-day'",
    );
    await worker.scheduled({}, env);
    assert.equal(
      env.raw
        .prepare("SELECT COUNT(*) AS n FROM jobs WHERE status='queued'")
        .get().n,
      1,
    );
    await request(
      env,
      "/device/poll",
      "POST",
      { snapshot: { ...snapshot(), local_schedule: true }, paused: true },
      d,
    );
    assert.equal(
      (
        await request(
          env,
          "/schedule",
          "PUT",
          { enabled: true, time: "20:00" },
          auth,
        )
      ).status,
      400,
    );
  } finally {
    env.close();
  }
});
test("rate limiting blocks repeated auth attempts", async () => {
  const env = localEnv();
  try {
    for (let i = 0; i < 30; i++)
      await request(env, "/auth/login", "POST", {
        email: "invalid",
        password: "invalid",
      });
    assert.equal(
      (
        await request(env, "/auth/login", "POST", {
          email: "invalid",
          password: "invalid",
        })
      ).status,
      429,
    );
  } finally {
    env.close();
  }
});

test("full manual queue still drains when a daily check is due", async () => {
  const env = localEnv();
  try {
    const { auth, d } = await seed(env);
    for (let i = 0; i < 30; i++)
      assert.equal((await enqueue(env, auth)).status, 202);
    await request(
      env,
      "/schedule",
      "PUT",
      { enabled: true, time: "00:00" },
      auth,
    );
    const response = await request(env, "/device/poll", "POST", {}, d);
    assert.equal(response.status, 200);
    assert.ok(response.body.job);
    assert.equal(
      env.raw
        .prepare("SELECT COUNT(*) AS n FROM jobs WHERE status='running'")
        .get().n,
      1,
    );
  } finally {
    env.close();
  }
});

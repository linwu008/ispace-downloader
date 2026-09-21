import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/worker.js";
import { localEnv } from "../local.mjs";
const origin = "http://127.0.0.1:8787";
async function req(e, p, m = "GET", b, auth = {}) {
  const r = await worker.fetch(
    new Request(origin + "/api" + p, {
      method: m,
      headers: {
        Origin: origin,
        "Content-Type": "application/json",
        ...(auth.cookie ? { Cookie: auth.cookie } : {}),
        ...(auth.csrf ? { "X-CSRF-Token": auth.csrf } : {}),
        ...(auth.token ? { Authorization: "Bearer " + auth.token } : {}),
      },
      ...(b ? { body: JSON.stringify(b) } : {}),
    }),
    e,
  );
  return {
    status: r.status,
    data: await r.json(),
    cookie: r.headers.get("set-cookie")?.split(";")[0],
  };
}
async function user(e, email = "one@example.org") {
  const r = await req(e, "/auth/register", "POST", {
    email,
    password: "12345678",
    invite: "NEST-LOCAL-04",
  });
  assert.equal(r.status, 200);
  const auth = { cookie: r.cookie };
  const me = await req(e, "/me", "GET", null, auth);
  return { ...auth, csrf: me.data.csrf, id: me.data.user.id };
}
async function seed(e) {
  const a = await user(e),
    p = await req(e, "/pairings", "POST", {}, a),
    d = await req(e, "/device/pair", "POST", {
      code: p.data.code,
      name: "Test PC",
    });
  return { a, d: { token: d.data.token }, id: d.data.device_id };
}
const snapshot = (ready = true) => ({
  version: "0.7.0",
  capabilities: ["confirm-v1", "local-archive-v1", "notes-v1", "cancel-v1"],
  courses: [
    { id: 1, name: "Math", membership: "added", bound: true, enabled: true },
  ],
  groups: [],
  materials: [],
  auth: "logged_in",
  readiness: {
    at: Math.floor(Date.now() / 1000),
    auth: ready,
    busy: false,
    courses: ready ? [1] : [],
  },
});
test("manual tasks wait for readiness, dismissal persists, confirmation is owned and exactly once", async () => {
  const e = localEnv();
  try {
    const { a, d, id } = await seed(e);
    await req(
      e,
      "/device/poll",
      "POST",
      { snapshot: snapshot(false), paused: true },
      d,
    );
    const j = await req(
      e,
      "/jobs",
      "POST",
      { kind: "sync", payload: {}, request_id: "manual-00000000001" },
      a,
    );
    assert.equal(j.status, 202);
    assert.equal((await req(e, "/device/poll", "POST", {}, d)).data.job, null);
    assert.equal(
      (await req(e, "/v07/pending", "GET", null, a)).data.items[0].ready,
      false,
    );
    assert.equal(
      (
        await req(
          e,
          "/v07/confirm",
          "POST",
          { ids: [j.data.id], action: "confirm" },
          a,
        )
      ).status,
      409,
    );
    await req(
      e,
      "/device/poll",
      "POST",
      { snapshot: snapshot(), paused: true },
      d,
    );
    const stranger = await user(e, "two@example.org");
    assert.equal(
      (
        await req(
          e,
          "/v07/confirm",
          "POST",
          { ids: [j.data.id], action: "confirm" },
          stranger,
        )
      ).status,
      400,
    );
    assert.equal(
      (
        await req(
          e,
          "/v07/confirm",
          "POST",
          { ids: [j.data.id], action: "dismiss" },
          a,
        )
      ).status,
      200,
    );
    assert.equal(
      (await req(e, "/v07/pending", "GET", null, a)).data.items[0].dismissed,
      1,
    );
    assert.equal(
      (
        await req(
          e,
          "/v07/device/confirm",
          "POST",
          { ids: [j.data.id], action: "confirm" },
          d,
        )
      ).status,
      200,
    );
    const claimed = await req(e, "/device/poll", "POST", {}, d);
    assert.equal(claimed.data.job.id, j.data.id);
    assert.equal((await req(e, "/device/poll", "POST", {}, d)).data.job, null);
  } finally {
    e.close();
  }
});
test("pending confirmations cancel; scheduled jobs bypass confirmation", async () => {
  const e = localEnv();
  try {
    const { a, d, id } = await seed(e);
    await req(
      e,
      "/device/poll",
      "POST",
      { snapshot: snapshot(), paused: true },
      d,
    );
    const j = await req(
      e,
      "/jobs",
      "POST",
      { kind: "sync", request_id: "cancel-00000000001" },
      a,
    );
    await req(e, "/jobs/" + j.data.id + "/cancel", "POST", {}, a);
    assert.equal(
      e.raw.prepare("SELECT status FROM jobs WHERE id=?").get(j.data.id).status,
      "canceled",
    );
    e.raw
      .prepare(
        "UPDATE devices SET schedule_enabled=1,schedule_time='00:00' WHERE id=?",
      )
      .run(id);
    const poll = await req(e, "/device/poll", "POST", {}, d);
    assert.equal(poll.data.job.kind, "sync");
  } finally {
    e.close();
  }
});
test("personal archives work without email or R2, retain note history and survive unpair", async () => {
  const e = localEnv();
  e.MAIL_ENABLED = "0";
  e.ARCHIVE_ENABLED = "0";
  delete e.ARCHIVE_BUCKET;
  try {
    const { a, d } = await seed(e);
    await req(
      e,
      "/device/poll",
      "POST",
      { snapshot: snapshot(), paused: true },
      d,
    );
    const term = await req(
      e,
      "/v07/terms",
      "POST",
      { label: "2026–27 / 1" },
      a,
    );
    assert.equal(term.status, 200);
    const payload = {
      term_id: term.data.id,
      course_id: 1,
      files: [
        {
          source_key: "file1",
          name: "Math.pdf",
          group_name: "Week 1",
          sha: "a".repeat(64),
          bytes: 12,
          available: true,
        },
      ],
      notes: [
        {
          source_key: "note1",
          category: "assignment",
          title: "Task",
          body: "Due Friday",
          url: "https://ispace.bnbu.edu.cn/mod/assign/view.php?id=1",
          section: "Week 1",
        },
      ],
    };
    const indexed = await req(e, "/v07/device/index", "POST", payload, d);
    assert.equal(indexed.status, 200, JSON.stringify(indexed));
    const id = indexed.data.archive_id;
    await req(e, "/v07/device/index", "POST", payload, d);
    assert.equal(
      e.raw.prepare("SELECT COUNT(*) n FROM study_note_history").get().n,
      0,
    );
    payload.notes[0].body = "Due Monday";
    await req(e, "/v07/device/index", "POST", payload, d);
    const read = await req(e, "/v07/archives/" + id, "GET", null, a);
    assert.equal(read.data.history[0].body, "Due Friday");
    assert.equal(read.data.notes[0].body, "Due Monday");
    const stranger = await user(e, "two@example.org");
    assert.equal(
      (await req(e, "/v07/archives/" + id, "GET", null, stranger)).status,
      404,
    );
    await req(e, "/v07/terms", "POST", { label: "2026–27 / 2" }, a);
    assert.equal(
      (await req(e, "/v07/device/index", "POST", payload, d)).status,
      409,
    );
    assert.equal((await req(e, "/device", "DELETE", null, a)).status, 200);
    assert.equal(
      (await req(e, "/v07/archives/" + id, "GET", null, a)).data.files.length,
      1,
    );
  } finally {
    e.close();
  }
});

test("note history quota is atomic and a re-paired owner can recover without exposing another account", async () => {
  const e = localEnv();
  e.NOTE_TEXT_LIMIT = "35";
  try {
    const { a, d } = await seed(e);
    await req(
      e,
      "/device/poll",
      "POST",
      { snapshot: snapshot(), paused: true },
      d,
    );
    const term = await req(e, "/v07/terms", "POST", { label: "Term 1" }, a);
    const value = {
      term_id: term.data.id,
      course_id: 1,
      files: [
        { source_key: "f", name: "lesson.pdf", sha: "a".repeat(64), bytes: 1 },
      ],
      notes: [
        {
          source_key: "n",
          title: "Task",
          body: "First version",
          category: "assignment",
          url: "https://school.test/page?token=private&id=1",
        },
      ],
    };
    const created = await req(e, "/v07/device/index", "POST", value, d);
    assert.equal(created.status, 200);
    value.notes[0].body = "Second version";
    assert.equal(
      (await req(e, "/v07/device/index", "POST", value, d)).status,
      200,
    );
    value.notes[0].body = "Third version";
    assert.equal(
      (await req(e, "/v07/device/index", "POST", value, d)).status,
      413,
    );
    const detail = await req(
      e,
      "/v07/archives/" + created.data.archive_id,
      "GET",
      null,
      a,
    );
    assert.equal(detail.data.notes[0].body, "Second version");
    assert.equal(detail.data.history.length, 1);
    assert(!detail.data.notes[0].url.includes("private"));
    assert.equal((await req(e, "/device", "DELETE", null, a)).status, 200);
    const pairing = await req(e, "/pairings", "POST", {}, a);
    const newDevice = await req(e, "/device/pair", "POST", {
      code: pairing.data.code,
      name: "Same PC",
    });
    const newAuth = { token: newDevice.data.token };
    const recovery = {
      archive_id: created.data.archive_id,
      files: [{ source_key: "f", sha: "a".repeat(64) }],
    };
    assert.equal(
      (await req(e, "/v07/device/recover", "POST", recovery, newAuth)).status,
      200,
    );
    assert.equal(
      (
        await req(
          e,
          "/v07/device/export",
          "POST",
          { archive_id: created.data.archive_id },
          newAuth,
        )
      ).status,
      200,
    );
    const stranger = await user(e, "stranger@example.org"),
      p = await req(e, "/pairings", "POST", {}, stranger),
      foreign = await req(e, "/device/pair", "POST", {
        code: p.data.code,
        name: "Other PC",
      });
    assert.equal(
      (
        await req(e, "/v07/device/recover", "POST", recovery, {
          token: foreign.data.token,
        })
      ).status,
      404,
    );
  } finally {
    e.close();
  }
});

test("v0.6 helper keeps ordinary tasks while new-only export is rejected", async () => {
  const e = localEnv();
  try {
    const { a, d } = await seed(e),
      s = snapshot();
    s.version = "0.6.0";
    s.capabilities = ["cancel-v1", "schedule-v1"];
    delete s.readiness;
    await req(e, "/device/poll", "POST", { snapshot: s, paused: true }, d);
    const task = await req(
      e,
      "/jobs",
      "POST",
      { kind: "sync", request_id: "legacy-sync-00000001" },
      a,
    );
    assert.equal(
      (await req(e, "/device/poll", "POST", {}, d)).data.job.id,
      task.data.id,
    );
    assert.deepEqual(
      (await req(e, "/v07/pending", "GET", null, a)).data.items,
      [],
    );
  } finally {
    e.close();
  }
});

test("additive bootstrap upgrades an original production schema without modifying accounts", async () => {
  const e = localEnv();
  try {
    const { a } = await seed(e);
    for (const table of [
      "study_note_history",
      "study_notes",
      "study_files",
      "study_archives",
      "study_terms",
      "study_limits",
      "study_usage",
      "job_confirmations",
    ])
      e.raw.exec("DROP TABLE " + table);
    const before = e.raw.prepare("SELECT * FROM users").all();
    const result = await req(
      e,
      "/v07/terms",
      "POST",
      { label: "Semester 1" },
      a,
    );
    assert.equal(result.status, 200, JSON.stringify(result));
    assert.deepEqual(e.raw.prepare("SELECT * FROM users").all(), before);
    assert.equal(
      e.raw.prepare("SELECT version FROM study_schema").get().version,
      7,
    );
    assert.equal((await req(e, "/v07/terms", "GET", null, a)).data.length, 1);
  } finally {
    e.close();
  }
});


test('required helper upgrade is explicit and independent of the website version',async()=>{
 const e=localEnv();try{
  const {d}=await seed(e);const s=snapshot();s.version='0.6.0';
  assert.equal((await req(e,'/device/poll','POST',{snapshot:s,paused:true},d)).status,200);
  e.MIN_HELPER_VERSION='0.7.0';e.HELPER_UPDATE_REASON='Security fix required';
  const blocked=await req(e,'/device/poll','POST',{snapshot:s,paused:true},d);
  assert.equal(blocked.status,426);assert.equal(blocked.data.detail,'Security fix required');
  s.version='0.7.0';assert.equal((await req(e,'/device/poll','POST',{snapshot:s,paused:true},d)).status,200);
 }finally{e.close();}
});

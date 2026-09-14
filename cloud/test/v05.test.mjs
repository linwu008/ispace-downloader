import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/worker.js";
import { localEnv } from "../local.mjs";
import { advanceKnowledge } from "../src/knowledge.js";
const origin = "http://127.0.0.1:8787";
async function req(e, path, method = "GET", body, auth = {}) {
  const r = await worker.fetch(
    new Request(origin + "/api" + path, {
      method,
      headers: {
        Origin: origin,
        "Content-Type": "application/json",
        ...(auth.cookie ? { Cookie: auth.cookie } : {}),
        ...(auth.csrf ? { "X-CSRF-Token": auth.csrf } : {}),
        ...(auth.token ? { Authorization: "Bearer " + auth.token } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    }),
    e,
  );
  return {
    status: r.status,
    body: await r.json(),
    cookie: r.headers.get("set-cookie")?.split(";")[0],
  };
}
async function account(e, email = "one@test.example") {
  const r = await req(e, "/auth/register", "POST", {
    email,
    password: "12345678",
    invite: "NEST-LOCAL-04",
  });
  assert.equal(r.status, 200, JSON.stringify(r.body));
  const a = { cookie: r.cookie };
  const me = await req(e, "/me", "GET", null, a);
  a.csrf = me.body.csrf;
  a.id = me.body.user.id;
  e.raw
    .prepare("INSERT INTO account_profiles VALUES (?,1,?)")
    .run(a.id, "trial");
  return a;
}
function bucket() {
  const objects = new Map();
  return {
    objects,
    async put(k, v) {
      objects.set(
        k,
        typeof v === "string" ? new TextEncoder().encode(v) : new Uint8Array(v),
      );
    },
    async head(k) {
      return objects.has(k) ? { size: objects.get(k).length } : null;
    },
    async delete(k) {
      objects.delete(k);
    },
    async get(k) {
      return objects.has(k)
        ? {
            body: objects.get(k),
            text: async () => new TextDecoder().decode(objects.get(k)),
          }
        : null;
    },
  };
}
async function fixture() {
  const e = localEnv();
  e.ARCHIVE_BUCKET = bucket();
  const a = await account(e);
  const code = (await req(e, "/pairings", "POST", {}, a)).body.code;
  const d = (await req(e, "/device/pair", "POST", { code, name: "PC" })).body;
  await req(
    e,
    "/device/poll",
    "POST",
    {
      paused: true,
      snapshot: {
        version: "0.5.0",
        capabilities: ["archive-v1"],
        courses: [{ id: 1, name: "Course", membership: "added", bound: true }],
        groups: [],
        materials: [],
      },
    },
    d,
  );
  const archive = await req(
    e,
    "/archives",
    "POST",
    { semester: "2026 Fall", course_id: 1, automatic: true },
    a,
  );
  assert.equal(archive.status, 200, JSON.stringify(archive.body));
  return { e, a, d, id: archive.body.id };
}
test("eight-character password and reset invalidates sessions; token is single use", async () => {
  const e = localEnv(),
    a = await account(e),
    mail = [];
  e.MAILER = { send: async (m) => mail.push(m) };
  assert.equal(
    (await req(e, "/auth/forgot", "POST", { email: "one@test.example" }))
      .status,
    200,
  );
  const token = mail[0].text.match(/#reset\/([a-f0-9]+)/)[1];
  assert.equal(
    (await req(e, "/auth/token", "POST", { token, password: "abcdefgh" }))
      .status,
    200,
  );
  assert.equal((await req(e, "/me", "GET", null, a)).status, 401);
  assert.equal(
    (await req(e, "/auth/token", "POST", { token, password: "abcdefgh" }))
      .status,
    400,
  );
  e.close();
});
test("archive reservations enforce quota, upload integrity and private access", async () => {
  const { e, a, d, id } = await fixture();
  e.USER_STORAGE_BYTES = "1000004";
  const bytes = new TextEncoder().encode("test");
  const sha = Buffer.from(
    await crypto.subtle.digest("SHA-256", bytes),
  ).toString("hex");
  const prepared = await req(
    e,
    "/device/archive/prepare",
    "POST",
    {
      archive_id: id,
      source_key: "one",
      name: "a.txt",
      group_name: "Week 1",
      bytes: 4,
      sha,
    },
    d,
  );
  assert.equal(prepared.status, 200, JSON.stringify(prepared.body));
  assert.equal(
    (
      await req(
        e,
        "/device/archive/prepare",
        "POST",
        { archive_id: id, source_key: "two", name: "b.txt", bytes: 4, sha },
        d,
      )
    ).status,
    409,
  );
  const upload = await worker.fetch(
    new Request(
      origin + "/api/device/archive/upload/" + prepared.body.upload_id,
      {
        method: "PUT",
        headers: { Authorization: "Bearer " + d.token, "Content-Length": "4" },
        body: bytes,
      },
    ),
    e,
  );
  assert.equal(upload.status, 200, await upload.text());
  const committed = await req(
    e,
    "/device/archive/commit",
    "POST",
    {
      upload_id: prepared.body.upload_id,
      parts: [{ locator: "正文", text: "test" }],
    },
    d,
  );
  assert.equal(committed.status, 200, JSON.stringify(committed.body));
  const other = await account(e, "other@test.example");
  assert.equal(
    (await req(e, "/archives/" + id, "GET", null, other)).status,
    403,
  );
  const share = (await req(e, "/archives/" + id + "/shares", "POST", {}, a))
    .body;
  const token = share.url.split("?share=")[1];
  assert.equal(
    (await req(e, "/archives/" + id + "?share=" + token, "GET", null, other))
      .status,
    200,
  );
  await req(e, "/archives/" + id + "/shares/" + share.id, "DELETE", null, a);
  assert.equal(
    (await req(e, "/archives/" + id + "?share=" + token, "GET", null, other))
      .status,
    403,
  );
  assert.equal(
    (
      await req(
        e,
        "/archives/" + id + "/question",
        "POST",
        { question: "test" },
        a,
      )
    ).status,
    403,
  );
  e.close();
});
test("summary completes with sources and disabled questions never call AI", async () => {
  const { e, a, id } = await fixture();
  let calls = 0;
  e.AI = {
    run: async () => {
      calls++;
      return { response: "Course summary [a.txt 正文]" };
    },
  };
  e.raw
    .prepare("INSERT INTO archive_files VALUES (?,?,?,?,?,?,?,?,?,?)")
    .run(
      "f",
      id,
      "s",
      "a.txt",
      "Week 1",
      "key",
      4,
      "digest",
      JSON.stringify([{ locator: "正文", text: "test concept" }]),
      1,
    );
  assert.equal(
    (
      await req(
        e,
        "/archives/" + id + "/question",
        "POST",
        { question: "test" },
        a,
      )
    ).status,
    403,
  );
  assert.equal(calls, 0);
  const j = await req(e, "/archives/" + id + "/summary", "POST", {}, a);
  assert.equal(j.status, 200);
  for (let i = 0; i < 3; i++) await advanceKnowledge(e);
  const summary = e.raw.prepare("SELECT * FROM summaries").get();
  assert.equal(summary.status, "done", summary.result);
  assert.equal(JSON.parse(summary.result).sources[0].file_id, "f");
  e.QA_ENABLED = "1";
  assert.equal(
    (
      await req(
        e,
        "/archives/" + id + "/question",
        "POST",
        { question: "test" },
        a,
      )
    ).status,
    200,
  );
  e.close();
});


test('existing accounts remain usable while archives require verified email; expired links fail',async()=>{
 const e=localEnv(),a=await account(e),mail=[];e.MAILER={send:async m=>mail.push(m)};
 e.raw.prepare('UPDATE account_profiles SET verified=0 WHERE user_id=?').run(a.id);
 assert.equal((await req(e,'/me','GET',null,a)).status,200);
 assert.equal((await req(e,'/archives','GET',null,a)).status,403);
 await req(e,'/account/verify','POST',{},a);
 const token=mail[0].text.match(/#verify\/([a-f0-9]+)/)[1];
 e.raw.prepare('UPDATE mail_tokens SET expires=1').run();
 assert.equal((await req(e,'/auth/token','POST',{token})).status,400);
 e.raw.prepare('UPDATE mail_tokens SET expires=?').run(Math.floor(Date.now()/1000)+100);
 assert.equal((await req(e,'/auth/token','POST',{token})).status,200);
 assert.equal((await req(e,'/archives','GET',null,a)).status,200);e.close();
});

test('new semester freezes previous automatic archive and manual selection is retained',async()=>{
 const {e,a,d,id}=await fixture();
 await req(e,'/archives/'+id+'/selection','POST',{ids:[1,2]},a);
 assert.deepEqual((await req(e,'/archives/'+id,'GET',null,a)).body.selected_ids,[1,2]);
 const next=await req(e,'/archives','POST',{semester:'2027 Spring',course_id:1,automatic:true},a);assert.equal(next.status,200);
 const old=e.raw.prepare('SELECT * FROM archives WHERE id=?').get(id);assert.equal(old.automatic,0);assert.equal(old.device_id,null);
 const active=await req(e,'/device/archives','GET',null,d);assert.equal(active.body.length,1);assert.equal(active.body[0].id,next.body.id);e.close();
});

test('AI budget exhaustion retains the pending work without calling model',async()=>{
 const {e,a,id}=await fixture();let calls=0;e.AI={run:async()=>{calls++;return {response:'text'};}};e.AI_DAILY_NEURONS='1';
 e.raw.prepare('INSERT INTO archive_files VALUES (?,?,?,?,?,?,?,?,?,?)').run('f',id,'s','a.txt','Week 1','key',4,'digest',JSON.stringify([{locator:'page 1',text:'test concept'}]),1);
 await req(e,'/archives/'+id+'/summary','POST',{},a);await advanceKnowledge(e);
 assert.equal(calls,0);const j=e.raw.prepare('SELECT * FROM summaries').get();assert.equal(j.status,'queued');
 const progress=JSON.parse(await (await e.ARCHIVE_BUCKET.get(JSON.parse(j.progress).key)).text());assert.equal(progress.tasks[0].text,'test concept');e.close();
});


test("production without new services uses only v0.4 tables and rejects disabled APIs", async () => {
  const env = localEnv(":memory:");
  env.ARCHIVE_ENABLED = "0";
  env.MAIL_ENABLED = "0";
  let calls = 0;
  env.AI = { run: async () => {calls++; throw Error("must not call");} };
  for (const name of ["account_profiles", "mail_tokens", "archives", "archive_files", "uploads", "shares", "summaries", "usage_counters", "archive_selection", "archive_exclusions"]) env.raw.exec(`DROP TABLE IF EXISTS ${name}`);
  const config = await req(env, "/features");
  assert.equal(config.body.archive_enabled, false);
  assert.equal(config.body.mail_enabled, false);
  const registered = await req(env, "/auth/register", "POST", {email:"limited@example.test",password:"12345678",invite:"NEST-LOCAL-04"});
  assert.equal(registered.status,200);
  const auth={cookie:registered.cookie};
  assert.equal((await req(env,"/me","GET",null,auth)).status,200);
  for (const path of ["/archives", "/device/archives", "/archives/anything/question", "/auth/forgot", "/auth/token", "/account/verify"]) assert.equal((await req(env,path,"POST",{},auth)).status,503,path);
  assert.equal((await req(env,"/account","GET",null,auth)).status,200);
  await worker.scheduled({},env);
  assert.equal(calls,0);
  env.raw.close();
});

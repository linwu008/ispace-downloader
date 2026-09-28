import test from "node:test";
import assert from "node:assert/strict";
import "../public/experience.js";
const UX = globalThis.CourseNestExperience;
test("semester suggestion follows Shanghai date and preserves January in previous academic year", () => {
  assert.equal(
    UX.semester(new Date("2026-08-31T15:59:00Z")),
    "2025–2026 / 第二学期",
  );
  assert.equal(
    UX.semester(new Date("2026-08-31T16:00:00Z")),
    "2026–2027 / 第一学期",
  );
  assert.equal(
    UX.semester(new Date("2027-01-12T00:00:00Z")),
    "2026–2027 / 第一学期",
  );
  assert.equal(
    UX.semester(new Date("2027-02-01T00:00:00Z")),
    "2026–2027 / 第二学期",
  );
});
test("offline and stale connections never claim readiness or guess a cause", () => {
  const d = {
    online: true,
    last_seen: 1000,
    snapshot: {
      readiness: { at: 1000, auth: true, courses: [1] },
      courses: [{ id: 1, membership: "added", enabled: true, bound: true }],
    },
  };
  assert.equal(UX.connection(d, false, 1010).title, "电脑已准备好");
  assert.equal(UX.connection(d, true, 1010).title, "连接状态待刷新");
  assert.equal(UX.connection(d, false, 1090).title, "暂未收到电脑心跳");
  d.snapshot.paused = true;
  assert.equal(UX.connection(d, false, 1010).title, "助手已暂停");
  d.snapshot.paused = false;
  d.snapshot.readiness.auth = false;
  assert.equal(UX.connection(d, false, 1010).title, "请连接学校账号");
  d.snapshot.readiness.auth = true;
  d.snapshot.readiness.courses = [];
  assert.equal(
    UX.connection(d, false, 1010).title,
    "电脑在线 · 请检查课程目录",
  );
  delete d.snapshot.readiness;
  assert.equal(UX.connection(d, false, 1010).title, "电脑在线 · 等待就绪检查");
});
test("tasks report only known stages and completed counts, never invented live progress", () => {
  assert.equal(
    UX.task(
      {
        status: "running",
        kind: "sync",
        updated: 100,
        result: { downloaded: 12 },
      },
      800,
    ).stalled,
    true,
  );
  assert.equal(
    UX.task(
      {
        status: "running",
        kind: "sync",
        updated: 100,
        result: { downloaded: 12 },
      },
      800,
    ).counts,
    "",
  );
  assert.match(
    UX.task({
      status: "success",
      kind: "sync",
      result: { downloaded: 2, skipped: 3, failed: 0 },
    }).counts,
    /下载 2/,
  );
  assert.equal(
    UX.task({ status: "success", kind: "archive_export", result: {} }).counts,
    "",
  );
  assert.match(
    UX.error({ name: "TimeoutError", message: "signal timed out" }),
    /连接超时/,
  );
});

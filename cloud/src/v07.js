import { ensureStudySchema } from "./study_schema.js";
// Personal metadata is independent of object storage and device lifetimes.
export function createV07(h) {
  const {
    first,
    all,
    run,
    body,
    json,
    check,
    now,
    sha,
    session,
    deviceAuth,
    throttle,
    enqueue,
  } = h;
  const txt = (v, n = 200) => (typeof v === "string" ? v.slice(0, n) : "");
  function safeUrl(value) {
    try {
      const u = new URL(value);
      if (!["http:", "https:"].includes(u.protocol) || u.username || u.password)
        return "";
      for (const k of [...u.searchParams.keys()])
        if (
          ["sesskey", "token", "wstoken", "access_token", "auth"].includes(
            k.toLowerCase(),
          )
        )
          u.searchParams.delete(k);
      u.hash = "";
      return u.href.slice(0, 1800);
    } catch {
      return "";
    }
  }
  async function owned(env, user, id) {
    const a = await first(
      env,
      "SELECT * FROM study_archives WHERE id=? AND user_id=?",
      id,
      user,
    );
    check(a, "存档不存在", 404);
    return a;
  }
  function ready(d, j) {
    const s = JSON.parse(d.snapshot),
      r = s.readiness;
    return (
      !d.revoked &&
      now() - d.last_seen < 75 &&
      !s.paused &&
      r &&
      r.at <= now() + 10 &&
      now() - r.at < 90 &&
      !r.busy &&
      (j.kind === "archive_export" || r.auth) &&
      (j.kind === "archive_export" ||
        (j.payload.course_id
          ? r.courses?.includes(j.payload.course_id)
          : s.courses?.filter(
              (c) => c.membership === "added" && c.enabled && c.bound,
            ).length > 0 &&
            s.courses
              .filter((c) => c.membership === "added" && c.enabled && c.bound)
              .every((c) => r.courses?.includes(c.id))))
    );
  }
  async function pending(env, d) {
    if (!d) return [];
    const rows = await all(
      env,
      "SELECT j.id,j.kind,j.payload,c.dismissed FROM jobs j JOIN job_confirmations c ON c.job_id=j.id WHERE j.device_id=? AND j.status='awaiting_confirmation' ORDER BY j.created",
      d.id,
    );
    return rows
      .map((j) => ({ ...j, payload: JSON.parse(j.payload) }))
      .map((j) => ({ ...j, ready: !!ready(d, j) }));
  }
  async function decision(env, d, ids, action) {
    check(
      Array.isArray(ids) &&
        ids.length > 0 &&
        ids.length <= 30 &&
        ["confirm", "dismiss"].includes(action),
    );
    const rows = await pending(env, d);
    for (const id of ids) {
      const j = rows.find((j) => j.id === id);
      check(j, "任务已处理，请刷新", 409);
      if (action === "confirm")
        check(j.ready, "电脑尚未准备好，请检查学校登录和保存目录", 409);
    }
    await env.DB.batch(
      ids.flatMap((id) =>
        action === "dismiss"
          ? [
              env.DB.prepare(
                "UPDATE job_confirmations SET dismissed=1 WHERE job_id=?",
              ).bind(id),
            ]
          : [
              env.DB.prepare(
                "UPDATE job_confirmations SET confirmed=1,dismissed=1 WHERE job_id=?",
              ).bind(id),
              env.DB.prepare(
                "UPDATE jobs SET status='queued',updated=? WHERE id=? AND device_id=? AND status='awaiting_confirmation'",
              ).bind(now(), id, d.id),
            ],
      ),
    );
    return json({ ok: true });
  }
  async function detail(env, a) {
    const notes = await all(
      env,
      "SELECT * FROM study_notes WHERE archive_id=? ORDER BY section,category,title",
      a.id,
    );
    const history = await all(
      env,
      "SELECT h.* FROM study_note_history h JOIN study_notes n ON n.id=h.note_id WHERE n.archive_id=? ORDER BY h.created DESC",
      a.id,
    );
    const term = await first(
      env,
      "SELECT label FROM study_terms WHERE id=?",
      a.term_id,
    );
    return {
      ...a,
      semester: term?.label || "",
      files: await all(
        env,
        "SELECT * FROM study_files WHERE archive_id=? ORDER BY group_name,name",
        a.id,
      ),
      notes,
      history,
      storage: "local",
      cloud_available: false,
    };
  }
  async function route(request, env, url) {
    const p = url.pathname,
      m = request.method;
    if (!p.startsWith("/api/v07/")) return null;
    if (p === "/api/v07/update" && m === "GET")
      return json({
        version: env.HELPER_VERSION || "0.6.0",
        url: env.HELPER_DOWNLOAD_URL || null,
        sha256: env.HELPER_SHA256 || null,
        signature: env.HELPER_SIGNATURE || null,
        min_version: env.MIN_HELPER_VERSION || "0.4.0",
        reason: env.HELPER_UPDATE_REASON || "新增电脑功能与兼容性改进",
        site_version: "0.7.0",
      });
    await ensureStudySchema(env);
    if (p.startsWith("/api/v07/device/")) {
      const d = await deviceAuth(request, env),
        b = m === "POST" ? await body(request) : {};
      await throttle(env, "study-device:" + d.id, 600, 3600);
      if (p.endsWith("/confirm") && m === "POST")
        return decision(env, d, b.ids, b.action);
      if (p.endsWith("/config") && m === "POST")
        return json({
          term: await first(
            env,
            "SELECT * FROM study_terms WHERE user_id=? AND active=1",
            d.user_id,
          ),
          pending: await pending(env, d),
        });
      if (p.endsWith("/recover") && m === "POST") {
        const a = await owned(env, d.user_id, b.archive_id);
        check(
          Array.isArray(b.files) && b.files.length > 0 && b.files.length <= 500,
        );
        const matched = await first(
          env,
          `SELECT COUNT(*) n FROM json_each(?) j JOIN study_files f ON f.archive_id=? AND f.source_key=json_extract(j.value,'$.source_key') AND f.sha=json_extract(j.value,'$.sha')`,
          JSON.stringify(b.files),
          a.id,
        );
        check(matched.n === b.files.length, "本地文件与原存档不符", 409);
        const previous = await first(
          env,
          "SELECT revoked FROM devices WHERE id=?",
          a.device_id,
        );
        check(
          a.device_id === d.id || !previous || previous.revoked,
          "请先解除原设备",
          409,
        );
        await run(
          env,
          "UPDATE study_archives SET device_id=? WHERE id=?",
          d.id,
          a.id,
        );
        return json({ ok: true });
      }
      if (p.endsWith("/export") && m === "POST") {
        const a = await owned(env, d.user_id, b.archive_id);
        check(a.device_id === d.id, "请在存档原电脑导出", 409);
        return json(await detail(env, a));
      }
      if (p.endsWith("/index") && m === "POST") {
        const term = await first(
          env,
          "SELECT * FROM study_terms WHERE user_id=? AND active=1",
          d.user_id,
        );
        check(term && term.id === b.term_id, "请先在官网确认学期", 409);
        const c = JSON.parse(d.snapshot).courses?.find(
          (c) => c.id === b.course_id && c.membership === "added",
        );
        check(c, "课程不属于此设备", 403);
        check(
          Array.isArray(b.files) &&
            b.files.length <= 500 &&
            Array.isArray(b.notes) &&
            b.notes.length <= 100,
          "分批上传清单",
        );
        let a = await first(
          env,
          "SELECT * FROM study_archives WHERE user_id=? AND term_id=? AND course_id=?",
          d.user_id,
          term.id,
          c.id,
        );
        if (!a) {
          a = { id: crypto.randomUUID() };
          await run(
            env,
            "INSERT OR IGNORE INTO study_archives VALUES (?,?,?,?,?,?,?)",
            a.id,
            d.user_id,
            term.id,
            c.id,
            c.name,
            d.id,
            now(),
          );
          a = await first(
            env,
            "SELECT * FROM study_archives WHERE user_id=? AND term_id=? AND course_id=?",
            d.user_id,
            term.id,
            c.id,
          );
        }
        // Do not adopt another device's local file locations after a re-pair.
        check(
          a.device_id === d.id,
          "原存档保留在旧设备；请选择新学期或恢复原设备",
          409,
        );
        // Bulk SQL keeps each catalog chunk below Workers Free D1 query limits.
        const files = b.files.map((f) => {
          check(
            typeof f.source_key === "string" &&
              f.source_key.length <= 1800 &&
              /^[a-f0-9]{64}$/.test(f.sha) &&
              Number.isSafeInteger(f.bytes) &&
              f.bytes >= 0,
          );
          return {
            id: crypto.randomUUID(),
            source_key: f.source_key,
            name: txt(f.name),
            group_name: txt(f.group_name),
            sha: f.sha,
            bytes: f.bytes,
            available: f.available ? 1 : 0,
          };
        });
        const notes = await Promise.all(
          b.notes.map(async (n) => {
            check(
              [
                "announcement",
                "assignment",
                "attendance",
                "group",
                "page",
                "link",
                "overview",
              ].includes(n.category) &&
                typeof n.source_key === "string" &&
                n.source_key.length <= 1800,
            );
            const body = txt(n.body, 30000),
              title = txt(n.title),
              url = safeUrl(n.url),
              section = txt(n.section),
              partial = n.partial ? 1 : 0;
            return {
              id: crypto.randomUUID(),
              source_key: n.source_key,
              category: n.category,
              title,
              body,
              url,
              section,
              partial,
              hash: await sha(
                JSON.stringify([
                  body,
                  title,
                  url,
                  section,
                  partial,
                  n.category,
                ]),
              ),
            };
          }),
        );
        check(
          new Set(files.map((f) => f.source_key)).size === files.length &&
            new Set(notes.map((n) => n.source_key)).size === notes.length,
          "同一批次不能包含重复资料",
        );
        const fileJson = JSON.stringify(files),
          noteJson = JSON.stringify(notes),
          stamp = now();
        const counts = await first(
          env,
          `SELECT (SELECT COUNT(*) FROM study_files WHERE archive_id IN (SELECT id FROM study_archives WHERE user_id=?))+(SELECT COUNT(*) FROM json_each(?) j WHERE NOT EXISTS(SELECT 1 FROM study_files f WHERE f.archive_id=? AND f.source_key=json_extract(j.value,'$.source_key'))) n`,
          d.user_id,
          fileJson,
          a.id,
        );
        check(
          counts.n <= Number(env.NOTE_FILE_LIMIT || 20000),
          "个人资料清单达到容量限制",
          413,
        );
        const statements = [
          env.DB.prepare(
            "UPDATE study_limits SET user_chars=?,global_chars=? WHERE id=1",
          ).bind(
            Number(env.NOTE_TEXT_LIMIT || 5000000),
            Number(env.GLOBAL_NOTE_TEXT_LIMIT || 100000000),
          ),
          env.DB.prepare(
            `INSERT INTO study_files SELECT json_extract(value,'$.id'),?,json_extract(value,'$.source_key'),json_extract(value,'$.name'),json_extract(value,'$.group_name'),json_extract(value,'$.sha'),json_extract(value,'$.bytes'),json_extract(value,'$.available'),? FROM json_each(?) WHERE 1 ON CONFLICT(archive_id,source_key) DO UPDATE SET name=excluded.name,group_name=excluded.group_name,sha=excluded.sha,bytes=excluded.bytes,available=excluded.available,updated=excluded.updated`,
          ).bind(a.id, stamp, fileJson),
          env.DB.prepare(
            `INSERT OR IGNORE INTO study_note_history SELECT n.id||':'||n.hash||':'||n.updated,n.id,n.body,n.title,n.hash,n.updated FROM study_notes n JOIN json_each(?) j ON n.source_key=json_extract(j.value,'$.source_key') WHERE n.archive_id=? AND n.hash<>json_extract(j.value,'$.hash')`,
          ).bind(noteJson, a.id),
          env.DB.prepare(
            `INSERT INTO study_notes SELECT json_extract(value,'$.id'),?,json_extract(value,'$.source_key'),json_extract(value,'$.category'),json_extract(value,'$.title'),json_extract(value,'$.body'),json_extract(value,'$.url'),json_extract(value,'$.section'),json_extract(value,'$.partial'),json_extract(value,'$.hash'),? FROM json_each(?) WHERE 1 ON CONFLICT(archive_id,source_key) DO UPDATE SET category=excluded.category,title=excluded.title,body=excluded.body,url=excluded.url,section=excluded.section,partial=excluded.partial,hash=excluded.hash,updated=excluded.updated WHERE study_notes.hash<>excluded.hash`,
          ).bind(a.id, stamp, noteJson),
        ];
        try {
          await env.DB.batch(statements);
        } catch (error) {
          if (String(error).includes("study_text_quota"))
            check(false, "课程文字存储额度已满，已有内容仍可查看", 413);
          throw error;
        }
        return json({ archive_id: a.id });
      }
      check(false, "接口不存在", 404);
    }
    const u = await session(request, env, m !== "GET");
    const d = await first(
      env,
      "SELECT * FROM devices WHERE user_id=? AND revoked=0",
      u.id,
    );
    if (p === "/api/v07/terms") {
      if (m === "GET")
        return json(
          await all(
            env,
            "SELECT * FROM study_terms WHERE user_id=? ORDER BY created DESC",
            u.id,
          ),
        );
      if (m === "POST") {
        const b = await body(request),
          label = txt(b.label, 80).trim();
        check(label, "请输入学年和学期");
        let term = b.id
          ? await first(
              env,
              "SELECT * FROM study_terms WHERE id=? AND user_id=?",
              b.id,
              u.id,
            )
          : await first(
              env,
              "SELECT * FROM study_terms WHERE label=? AND user_id=?",
              label,
              u.id,
            );
        if (b.id) check(term, "学期不存在", 404);
        const id = term?.id || crypto.randomUUID();
        await env.DB.batch([
          env.DB.prepare(
            "UPDATE study_terms SET active=0 WHERE user_id=?",
          ).bind(u.id),
          env.DB.prepare(
            "INSERT INTO study_terms VALUES (?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,active=1",
          ).bind(id, u.id, label, now()),
        ]);
        return json({ id, label });
      }
    }
    if (p === "/api/v07/pending" && m === "GET")
      return json({ items: await pending(env, d) });
    if (p === "/api/v07/confirm" && m === "POST") {
      check(d, "请先配对电脑");
      const b = await body(request);
      return decision(env, d, b.ids, b.action);
    }
    if (p === "/api/v07/archives" && m === "GET")
      return json(
        await all(
          env,
          "SELECT a.*,t.label AS semester,(SELECT COUNT(*) FROM study_files f WHERE f.archive_id=a.id) file_count,(SELECT COUNT(*) FROM study_notes n WHERE n.archive_id=a.id) note_count FROM study_archives a JOIN study_terms t ON t.id=a.term_id WHERE a.user_id=? ORDER BY a.created DESC",
          u.id,
        ),
      );
    const match = p.match(/^\/api\/v07\/archives\/([\w-]+)(\/export|\/term)?$/);
    if (match) {
      const a = await owned(env, u.id, match[1]);
      if (m === "GET" && !match[2]) return json(await detail(env, a));
      if (m === "POST" && match[2] === "/term") {
        const b = await body(request);
        check(
          await first(
            env,
            "SELECT id FROM study_terms WHERE id=? AND user_id=?",
            b.term_id,
            u.id,
          ),
          "学期不存在",
        );
        check(
          !(await first(
            env,
            "SELECT id FROM study_archives WHERE user_id=? AND term_id=? AND course_id=? AND id<>?",
            u.id,
            b.term_id,
            a.course_id,
            a.id,
          )),
          "该学期已有此课程，不能覆盖",
          409,
        );
        await run(
          env,
          "UPDATE study_archives SET term_id=? WHERE id=?",
          b.term_id,
          a.id,
        );
        return json({ ok: true });
      }
      if (m === "POST" && match[2] === "/export") {
        check(d && a.device_id === d.id, "需在保存原文件的电脑上导出", 409);
        check(
          JSON.parse(d.snapshot).capabilities?.includes("local-archive-v1"),
          "此功能需要新版助手",
          409,
        );
        const b = await body(request);
        check(/^[\w-]{16,80}$/.test(b.request_id || ""));
        const id = await enqueue(
          env,
          d,
          "archive_export",
          { archive_id: a.id },
          u.id + ":export:" + b.request_id,
        );
        await env.DB.batch([
          env.DB.prepare(
            "INSERT OR IGNORE INTO job_confirmations(job_id) VALUES (?)",
          ).bind(id),
          env.DB.prepare(
            "UPDATE jobs SET status='awaiting_confirmation' WHERE id=? AND status='queued' AND attempts=0",
          ).bind(id),
        ]);
        return json({ id }, 202);
      }
    }
    check(false, "接口不存在", 404);
  }
  return { route, pending, ready };
}

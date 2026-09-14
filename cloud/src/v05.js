import { storageBudget } from "./budgets.js";
import { question } from "./knowledge.js";
// Account recovery and archive APIs share the existing session/device boundary.
export function createV05(h) {
  const {
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
  } = h;
  const limit = (env, key, fallback) => Number(env[key] || fallback);
  async function verified(env, user) {
    check(
      (
        await first(
          env,
          "SELECT verified FROM account_profiles WHERE user_id=?",
          user.id,
        )
      )?.verified,
      "请先验证邮箱",
      403,
    );
  }
  async function owner(env, id, user) {
    const a = await first(
      env,
      "SELECT * FROM archives WHERE id=? AND user_id=?",
      id,
      user.id,
    );
    check(a, "存档不存在", 404);
    return a;
  }
  async function readable(env, id, user, token) {
    const a = await first(env, "SELECT * FROM archives WHERE id=?", id);
    check(a, "存档不存在", 404);
    if (a.user_id !== user.id)
      check(
        await first(
          env,
          "SELECT id FROM shares WHERE archive_id=? AND revoked=0 AND (email=? OR token=?)",
          id,
          user.email,
          token ? await sha(token) : "",
        ),
        "没有访问权限",
        403,
      );
    return a;
  }
  async function send(env, user, kind) {
    check(
      env.MAIL_ENABLED !== "0" && (env.MAILER || (env.RESEND_API_KEY && env.MAIL_FROM)),
      "邮件服务尚未配置，请联系管理员",
      503,
    );
    const day = new Date().toISOString().slice(0, 10),
      month = day.slice(0, 7);
    await throttle(env, "mail-user:" + user.id, 3, 3600);
    for (const [key, max] of [
      ["mail-day:" + day, 90],
      ["mail-month:" + month, 2700],
    ]) {
      await run(env, "INSERT OR IGNORE INTO usage_counters VALUES (?,0)", key);
      check(
        await first(
          env,
          "UPDATE usage_counters SET amount=amount+1 WHERE key=? AND amount<? RETURNING amount",
          key,
          max,
        ),
        "发信额度暂时用完",
        429,
      );
    }
    const token = random(),
      hashed = await sha(token);
    await run(
      env,
      "INSERT INTO mail_tokens VALUES (?,?,?,?,0)",
      hashed,
      user.id,
      kind,
      now() + 1800,
    );
    const link = env.PUBLIC_ORIGIN + "/account.html#" + kind + "/" + token;
    const payload = {
      from: env.MAIL_FROM,
      to: [user.email],
      subject: kind === "reset" ? "CourseNest 密码重置" : "CourseNest 邮箱验证",
      text:
        "请在 30 分钟内打开以下链接：\n" +
        link +
        "\n如果不是你发起的操作，请忽略此邮件。",
    };
    try {
      if (env.MAILER) await env.MAILER.send(payload);
      else {
        const response = await fetch("https://api.resend.com/emails", {
          method: "POST",
          headers: {
            Authorization: "Bearer " + env.RESEND_API_KEY,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
        });
        check(response.ok, "邮件发送失败，请稍后重试", 503);
      }
    } catch (e) {
      await run(env, "DELETE FROM mail_tokens WHERE token=?", hashed);
      throw e;
    }
  }
  async function route(request, env, url) {
    const p = url.pathname,
      m = request.method;
    const archiveEnabled = env.ARCHIVE_ENABLED !== "0" && !!env.ARCHIVE_BUCKET;
    const mailEnabled = env.MAIL_ENABLED !== "0" && !!(env.MAILER || (env.RESEND_API_KEY && env.MAIL_FROM));
    if (p === "/api/features" && m === "GET") return json({archive_enabled: archiveEnabled, mail_enabled: mailEnabled, qa_enabled: archiveEnabled && env.QA_ENABLED === "1"});
    if (p.startsWith("/api/archives") || p.startsWith("/api/device/archive")) check(archiveEnabled, "学期存档、分享和知识总结暂未启用；电脑同步仍可正常使用。", 503);
    if (["/api/auth/forgot", "/api/auth/token", "/api/account/verify"].includes(p)) check(mailEnabled, "邮件服务暂未启用，暂时无法发送验证或密码重置邮件。", 503);
    if (p === "/api/account" && env.MAIL_ENABLED === "0" && !archiveEnabled) {
      await session(request, env, false);
      return json({verified:false, quota:0, qa_enabled:false, mail_enabled:false, archive_enabled:false});
    }
    if (p === "/api/helper/download" && env.DOWNLOAD_BUCKET)
      await storageBudget(env, "read", 2);
    if (p === "/api/helper/download" && m === "GET") {
      check(env.DOWNLOAD_BUCKET, "安装包尚未发布", 503);
      const release = await env.DOWNLOAD_BUCKET.get("release.json");
      check(release, "安装包尚未发布", 503);
      const meta = JSON.parse(await release.text());
      const file = await env.DOWNLOAD_BUCKET.get(meta.key);
      check(file, "安装包暂时不可用", 503);
      return new Response(file.body, {
        headers: {
          "Content-Type": "application/zip",
          "Content-Disposition":
            'attachment; filename="CourseNestHelper-0.5.0-windows-x64.zip"',
        },
      });
    }
    if (p === "/api/helper" && m === "GET" && env.DOWNLOAD_BUCKET) {
      const release = await env.DOWNLOAD_BUCKET.get("release.json");
      if (release) {
        const meta = JSON.parse(await release.text());
        return json({
          version: meta.version,
          url: "/api/helper/download",
          sha256: meta.sha256,
          platform: "Windows 10/11 x64",
        });
      }
    }
    if (p === "/api/helper" && m === "GET")
      return json({
        version: "0.5.0",
        url: env.HELPER_DOWNLOAD_URL || null,
        sha256: env.HELPER_SHA256 || null,
        platform: "Windows 10/11 x64",
        qa_enabled: env.QA_ENABLED === "1",
      });
    if (p === "/api/auth/forgot" && m === "POST") {
      check(
        env.MAIL_ENABLED !== "0" && (env.MAILER || (env.RESEND_API_KEY && env.MAIL_FROM)),
        "邮件服务尚未配置",
        503,
      );
      await throttle(
        env,
        "forgot:" + (request.headers.get("cf-connecting-ip") || "local"),
        5,
        3600,
      );
      const b = await body(request),
        user = await first(
          env,
          "SELECT * FROM users WHERE email=?",
          String(b.email || "")
            .trim()
            .toLowerCase(),
        );
      if (user) {
        try {
          await send(env, user, "reset");
        } catch {
          /* Identical response prevents account enumeration. */
        }
      }
      return json({ message: "如果该邮箱已注册，你将收到重置邮件。" });
    }
    if (p === "/api/auth/token" && m === "POST") {
      const b = await body(request),
        token = await sha(String(b.token || ""));
      const t = await first(
        env,
        "SELECT * FROM mail_tokens WHERE token=? AND used=0 AND expires>?",
        token,
        now(),
      );
      check(t, "链接已失效，请重新申请");
      let password, salt;
      if (t.kind === "reset") {
        check(
          typeof b.password === "string" &&
            b.password.length >= 8 &&
            b.password.length <= 128,
          "密码需要 8–128 个字符",
        );
        salt = random(16);
        password = await passwordHash(b.password, salt);
      }
      // D1 batch keeps consumption and account modification atomic, including retries.
      const statements =
        t.kind === "reset"
          ? [
              env.DB.prepare(
                "UPDATE users SET password=?,salt=? WHERE id=? AND EXISTS(SELECT 1 FROM mail_tokens WHERE token=? AND used=0 AND expires>?)",
              ).bind(password, salt, t.user_id, token, now()),
              env.DB.prepare(
                "DELETE FROM sessions WHERE user_id=? AND EXISTS(SELECT 1 FROM mail_tokens WHERE token=? AND used=0 AND expires>?)",
              ).bind(t.user_id, token, now()),
            ]
          : [
              env.DB.prepare(
                "INSERT INTO account_profiles(user_id,verified) SELECT ?,1 WHERE EXISTS(SELECT 1 FROM mail_tokens WHERE token=? AND used=0 AND expires>?) ON CONFLICT(user_id) DO UPDATE SET verified=1",
              ).bind(t.user_id, token, now()),
            ];
      statements.push(
        env.DB.prepare(
          "UPDATE mail_tokens SET used=1 WHERE user_id=? AND kind=?",
        ).bind(t.user_id, t.kind),
      );
      await env.DB.batch(statements);
      return json({
        message: t.kind === "reset" ? "密码已更新，请重新登录" : "邮箱已验证",
      });
    }
    if (p === "/api/account" || p === "/api/account/verify") {
      const user = await session(request, env, m !== "GET");
      if (p.endsWith("/verify") && m === "POST") {
        await send(env, user, "verify");
        return json({ message: "验证邮件已发送" });
      }
      check(m === "GET", "请求方法不支持", 405);
      return json({
        verified: !!(
          await first(
            env,
            "SELECT verified FROM account_profiles WHERE user_id=?",
            user.id,
          )
        )?.verified,
        quota: limit(env, "USER_STORAGE_BYTES", 250000000),
        qa_enabled: env.QA_ENABLED === "1",
      });
    }
    if (p.startsWith("/api/device/archive")) {
      const d = await deviceAuth(request, env),
        user = { id: d.user_id };
      await verified(env, user);
      check(env.ARCHIVE_BUCKET, "存档服务尚未配置", 503);
      await throttle(env, "archive-device:" + d.id, 300, 3600);
      await storageBudget(env, m === "GET" ? "read" : "write");
      if (p === "/api/device/archives" && m === "GET") {
        const archives = await all(
          env,
          "SELECT * FROM archives WHERE user_id=? AND device_id=?",
          d.user_id,
          d.id,
        );
        for (const a of archives)
          a.selected_ids = JSON.parse(
            (
              await first(
                env,
                "SELECT ids FROM archive_selection WHERE archive_id=?",
                a.id,
              )
            )?.ids || "[]",
          );
        return json(archives);
      }
      if (p === "/api/device/archive/prepare" && m === "POST") {
        const b = await body(request);
        const a = await owner(env, b.archive_id, user);
        check(a.device_id === d.id, "设备不匹配", 403);
        check(
          typeof b.source_key === "string" &&
            b.source_key.length <= 500 &&
            typeof b.name === "string" &&
            b.name.length <= 300,
          "文件标识无效",
        );
        check(
          Number.isSafeInteger(b.bytes) &&
            b.bytes > 0 &&
            b.bytes <= limit(env, "MAX_FILE_BYTES", 50000000),
          "文件超过上传限制",
        );
        check(/^[a-f0-9]{64}$/.test(b.sha), "文件校验值无效");
        if (
          await first(
            env,
            "SELECT source_key FROM archive_exclusions WHERE archive_id=? AND source_key=?",
            a.id,
            b.source_key,
          )
        )
          return json({ excluded: true });
        const old = await first(
          env,
          "SELECT * FROM archive_files WHERE archive_id=? AND source_key=?",
          a.id,
          b.source_key,
        );
        if (old?.sha === b.sha) return json({ unchanged: true });
        const pending = await first(
          env,
          "SELECT id FROM uploads WHERE archive_id=? AND source_key=? AND sha=? AND status='pending' AND expires>?",
          a.id,
          b.source_key,
          b.sha,
          now(),
        );
        if (pending) return json({ upload_id: pending.id });
        await run(
          env,
          "UPDATE uploads SET status='expired' WHERE archive_id=? AND source_key=? AND status='pending' AND expires<=?",
          a.id,
          b.source_key,
          now(),
        );
        check(
          !(await first(
            env,
            "SELECT id FROM uploads WHERE archive_id=? AND source_key=? AND status='pending'",
            a.id,
            b.source_key,
          )),
          "该文件已有上传任务，请稍后重试",
          409,
        );
        const id = random(16);
        const reserved = await first(
          env,
          `INSERT OR IGNORE INTO uploads SELECT ?,?,?,?,?,?,?,?,?,'pending' WHERE
          (SELECT COALESCE(SUM(f.bytes),0) FROM archive_files f JOIN archives a ON a.id=f.archive_id WHERE a.user_id=?)+(SELECT COALESCE(SUM(bytes+1000000),0) FROM uploads WHERE user_id=? AND status='pending' AND expires>?)+?<=?
          AND (SELECT COALESCE(SUM(bytes),0) FROM archive_files)+(SELECT COALESCE(SUM(bytes+1000000),0) FROM uploads WHERE status='pending' AND expires>?)+?<=? RETURNING id`,
          id,
          d.user_id,
          a.id,
          b.source_key,
          b.name,
          String(b.group_name || "未分组").slice(0, 200),
          b.bytes,
          b.sha,
          now() + 3600,
          d.user_id,
          d.user_id,
          now(),
          b.bytes + 1000000,
          limit(env, "USER_STORAGE_BYTES", 250000000),
          now(),
          b.bytes + 1000000,
          limit(env, "GLOBAL_STORAGE_BYTES", 8000000000),
        );
        check(reserved, "存档空间已满，请清理后重试", 409);
        return json({ upload_id: id });
      }
      const match = p.match(/^\/api\/device\/archive\/upload\/([a-f0-9]+)$/);
      if (match && m === "PUT") {
        const u = await first(
          env,
          "SELECT * FROM uploads WHERE id=? AND user_id=? AND status='pending' AND expires>?",
          match[1],
          d.user_id,
          now(),
        );
        check(u, "上传已过期", 409);
        check(
          Number(request.headers.get("content-length")) === u.bytes,
          "上传长度不匹配",
        );
        const reader = request.body.getReader();
        const data = new Uint8Array(u.bytes);
        let size = 0;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          check(size + value.byteLength <= u.bytes, "上传超过预留大小", 413);
          data.set(value, size);
          size += value.byteLength;
        }
        check(size === u.bytes, "文件不完整");
        const digest = Array.from(
          new Uint8Array(await crypto.subtle.digest("SHA-256", data)),
          (n) => n.toString(16).padStart(2, "0"),
        ).join("");
        check(digest === u.sha, "文件校验失败");
        await env.ARCHIVE_BUCKET.put("uploads/" + u.id, data);
        return json({ ok: true });
      }
      if (p === "/api/device/archive/commit" && m === "POST") {
        const b = await body(request);
        const completed = await first(
          env,
          "SELECT id FROM uploads WHERE id=? AND user_id=? AND status='done'",
          b.upload_id,
          d.user_id,
        );
        if (completed) return json({ ok: true });
        const u = await first(
          env,
          "SELECT * FROM uploads WHERE id=? AND user_id=? AND status='pending' AND expires>?",
          b.upload_id,
          d.user_id,
          now(),
        );
        check(u, "上传已过期", 409);
        check(await env.ARCHIVE_BUCKET.head("uploads/" + u.id), "请先上传文件");
        const parts = Array.isArray(b.parts) ? b.parts : [];
        check(
          parts.length <= 2000 &&
            new TextEncoder().encode(JSON.stringify(parts)).length <= 1000000,
          "提取文本过大",
        );
        check(
          parts.every(
            (x) => typeof x.text === "string" && typeof x.locator === "string",
          ),
          "文本格式无效",
        );
        await env.ARCHIVE_BUCKET.put("text/" + u.id, JSON.stringify(parts));
        const previous = await first(
          env,
          "SELECT object_key FROM archive_files WHERE archive_id=? AND source_key=?",
          u.archive_id,
          u.source_key,
        );
        await env.DB.batch([
          env.DB.prepare(
            `INSERT INTO archive_files VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(archive_id,source_key) DO UPDATE SET name=excluded.name,group_name=excluded.group_name,object_key=excluded.object_key,bytes=excluded.bytes,sha=excluded.sha,text_json=excluded.text_json,updated=excluded.updated`,
          ).bind(
            random(16),
            u.archive_id,
            u.source_key,
            u.name,
            u.group_name,
            "uploads/" + u.id,
            u.bytes + new TextEncoder().encode(JSON.stringify(parts)).length,
            u.sha,
            JSON.stringify({ key: "text/" + u.id, parts: parts.length }),
            now(),
          ),
          env.DB.prepare("UPDATE uploads SET status='done' WHERE id=?").bind(
            u.id,
          ),
          env.DB.prepare(
            "UPDATE summaries SET status='stale' WHERE archive_id=? AND status IN ('done','queued','running')",
          ).bind(u.archive_id),
        ]);
        if (previous && previous.object_key !== "uploads/" + u.id) {
          await env.ARCHIVE_BUCKET.delete(previous.object_key);
          await env.ARCHIVE_BUCKET.delete(
            previous.object_key.replace("uploads/", "text/"),
          );
        }
        return json({ ok: true });
      }
      return null;
    }
    if (!p.startsWith("/api/archives")) return null;
    const user = await session(request, env, m !== "GET");
    await verified(env, user);
    await throttle(env, "archives:" + user.id, 120, 60);
    await storageBudget(env, m === "GET" ? "read" : "write");
    if (p === "/api/archives") {
      if (m === "GET")
        return json(
          await all(
            env,
            "SELECT a.*,COALESCE(SUM(f.bytes),0) bytes,COUNT(f.id) files FROM archives a LEFT JOIN archive_files f ON f.archive_id=a.id WHERE a.user_id=? OR EXISTS(SELECT 1 FROM shares s WHERE s.archive_id=a.id AND s.email=? AND s.revoked=0) GROUP BY a.id ORDER BY a.created DESC",
            user.id,
            user.email,
          ),
        );
      if (m === "POST") {
        const b = await body(request);
        check(
          typeof b.semester === "string" &&
            b.semester.length > 0 &&
            b.semester.length <= 80,
          "请填写学年和学期",
        );
        const d = await first(
          env,
          "SELECT * FROM devices WHERE user_id=? AND revoked=0",
          user.id,
        );
        check(d, "请先连接电脑");
        check(
          JSON.parse(d.snapshot).capabilities?.includes("archive-v1"),
          "请先升级电脑助手到 v0.5",
        );
        const c = JSON.parse(d.snapshot).courses?.find(
          (x) => x.id === b.course_id,
        );
        check(c, "请选择电脑清单中的课程");
        const existing = await first(
          env,
          "SELECT id FROM archives WHERE user_id=? AND semester=? AND course_id=? AND device_id=?",
          user.id,
          b.semester,
          c.id,
          d.id,
        );
        if (existing) return json(existing);
        const id = random(16);
        await env.DB.batch([
          env.DB.prepare(
            "UPDATE archives SET automatic=0,device_id=NULL WHERE user_id=? AND device_id=? AND course_id=? AND semester<>?",
          ).bind(user.id, d.id, c.id, b.semester),
          env.DB.prepare(
            "INSERT INTO archives VALUES (?,?,?,?,?,?,?,?,?)",
          ).bind(
            id,
            user.id,
            b.semester,
            c.name,
            d.id,
            c.id,
            b.automatic ? 1 : 0,
            ["zh", "en", "bilingual"].includes(b.language) ? b.language : "zh",
            now(),
          ),
        ]);
        return json({ id });
      }
    }
    const match = p.match(/^\/api\/archives\/([a-f0-9]+)(?:\/(.*))?$/);
    check(match, "接口不存在", 404);
    const id = match[1],
      action = match[2] || "",
      a = await readable(env, id, user, url.searchParams.get("share"));
    if (m !== "GET") await owner(env, id, user);
    if (!action && m === "DELETE") {
      const files = await all(
        env,
        "SELECT object_key FROM archive_files WHERE archive_id=?",
        id,
      );
      for (const f of files) {
        await env.ARCHIVE_BUCKET.delete(f.object_key);
        await env.ARCHIVE_BUCKET.delete(
          f.object_key.replace("uploads/", "text/"),
        );
      }
      await env.DB.batch(
        [
          "archive_files",
          "shares",
          "summaries",
          "archive_selection",
          "archive_exclusions",
          "uploads",
        ]
          .map((t) =>
            env.DB.prepare("DELETE FROM " + t + " WHERE archive_id=?").bind(id),
          )
          .concat([env.DB.prepare("DELETE FROM archives WHERE id=?").bind(id)]),
      );
      return json({ ok: true });
    }
    if (!action && m === "PATCH") {
      const b = await body(request);
      check(["zh", "en", "bilingual"].includes(b.language), "总结语言无效");
      await run(
        env,
        "UPDATE archives SET automatic=?,language=? WHERE id=?",
        b.automatic ? 1 : 0,
        b.language,
        id,
      );
      return json({ ok: true });
    }
    if (!action && m === "GET")
      return json({
        archive: a,
        selected_ids: JSON.parse(
          (
            await first(
              env,
              "SELECT ids FROM archive_selection WHERE archive_id=?",
              id,
            )
          )?.ids || "[]",
        ),
        files: await all(
          env,
          "SELECT id,name,group_name,bytes,updated,text_json FROM archive_files WHERE archive_id=?",
          id,
        ),
        summaries: await all(
          env,
          "SELECT id,status,result,updated FROM summaries WHERE archive_id=? ORDER BY updated DESC",
          id,
        ),
      });
    if (action === "selection" && m === "POST") {
      const b = await body(request);
      check(
        Array.isArray(b.ids) &&
          b.ids.length <= 10000 &&
          b.ids.every(Number.isSafeInteger),
        "资料选择无效",
      );
      await run(
        env,
        "INSERT INTO archive_selection VALUES (?,?) ON CONFLICT(archive_id) DO UPDATE SET ids=excluded.ids",
        id,
        JSON.stringify(b.ids),
      );
      return json({ ok: true });
    }
    if (action === "shares" && m === "POST") {
      const b = await body(request),
        email = String(b.email || "")
          .trim()
          .toLowerCase(),
        token = email ? null : random();
      if (email)
        check(
          await first(env, "SELECT id FROM users WHERE email=?", email),
          "接收者需先注册",
        );
      const sid = random(16);
      await run(
        env,
        "INSERT INTO shares VALUES (?,?,?,?,0)",
        sid,
        id,
        email || null,
        token ? await sha(token) : null,
      );
      return json({
        id: sid,
        url: token
          ? env.PUBLIC_ORIGIN + "/archives.html#" + id + "?share=" + token
          : null,
      });
    }
    if (action === "shares" && m === "GET") {
      await owner(env, id, user);
      return json(
        await all(
          env,
          "SELECT id,email,revoked FROM shares WHERE archive_id=?",
          id,
        ),
      );
    }
    if (action.startsWith("shares/") && m === "DELETE") {
      await run(
        env,
        "UPDATE shares SET revoked=1 WHERE id=? AND archive_id=?",
        action.slice(7),
        id,
      );
      return json({ ok: true });
    }
    if (action.startsWith("files/") && m === "GET") {
      const f = await first(
        env,
        "SELECT * FROM archive_files WHERE id=? AND archive_id=?",
        action.slice(6),
        id,
      );
      check(f, "资料不存在", 404);
      const object = await env.ARCHIVE_BUCKET.get(f.object_key);
      check(object, "文件暂时不可用", 404);
      return new Response(object.body, {
        headers: {
          "Content-Type": "application/octet-stream",
          "Content-Disposition":
            "attachment; filename*=UTF-8''" + encodeURIComponent(f.name),
        },
      });
    }
    if (action.startsWith("files/") && m === "DELETE") {
      const f = await first(
        env,
        "SELECT object_key,source_key FROM archive_files WHERE id=? AND archive_id=?",
        action.slice(6),
        id,
      );
      check(f, "文件不存在", 404);
      await run(
        env,
        "INSERT OR IGNORE INTO archive_exclusions VALUES (?,?)",
        id,
        f.source_key,
      );
      await env.ARCHIVE_BUCKET.delete(f.object_key);
      await env.ARCHIVE_BUCKET.delete(
        f.object_key.replace("uploads/", "text/"),
      );
      await run(
        env,
        "DELETE FROM archive_files WHERE id=? AND archive_id=?",
        action.slice(6),
        id,
      );
      await run(
        env,
        "UPDATE summaries SET status='stale' WHERE archive_id=?",
        id,
      );
      return json({ ok: true });
    }
    if (action === "summary" && m === "POST") {
      const files = await all(
        env,
        "SELECT sha FROM archive_files WHERE archive_id=? ORDER BY id",
        id,
      );
      check(files.length, "请先上传资料");
      const fingerprint = await sha(JSON.stringify(files) + a.language),
        existing = await first(
          env,
          "SELECT id FROM summaries WHERE archive_id=? AND fingerprint=? AND status IN ('queued','running','done')",
          id,
          fingerprint,
        );
      if (existing) return json(existing);
      const sid = random(16);
      await run(
        env,
        "INSERT INTO summaries VALUES (?,?,?,?,?,'queued','','{}',?)",
        sid,
        id,
        user.id,
        fingerprint,
        a.language,
        now(),
      );
      return json({ id: sid });
    }
    if (action === "question" && m === "POST") {
      check(env.QA_ENABLED === "1", "资料问答尚未开放", 403);
      const b = await body(request);
      check(
        typeof b.question === "string" &&
          b.question.length > 0 &&
          b.question.length <= 1000,
        "问题需要 1–1000 个字符",
      );
      await throttle(env, "qa:" + user.id, 10, 3600);
      return json(await question(env, id, b.question, a.language));
    }
    check(false, "接口不存在", 404);
  }
  return { route, send };
}

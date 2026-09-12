import { DatabaseSync } from "node:sqlite";
import { readFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";
export const root = dirname(fileURLToPath(import.meta.url));
export function localEnv(
  filename = ":memory:",
  origin = "http://127.0.0.1:8787",
) {
  if (filename !== ":memory:")
    mkdirSync(dirname(filename), { recursive: true });
  const db = new DatabaseSync(filename);
  db.exec(readFileSync(resolve(root, "migrations/0001_initial.sql"), "utf8"));
  db.exec("PRAGMA journal_mode=WAL");
  const prepare = (sql) => ({
    sql,
    args: [],
    bind(...args) {
      return { ...this, args };
    },
    async first(column) {
      const row = db.prepare(this.sql).get(...this.args);
      return row ? (column ? row[column] : row) : null;
    },
    async all() {
      return { results: db.prepare(this.sql).all(...this.args) };
    },
    async run() {
      return { meta: db.prepare(this.sql).run(...this.args) };
    },
  });
  return {
    LOCAL_DEV: "1",
    PUBLIC_ORIGIN: origin,
    INVITE_CODE: process.env.COURSENEST_INVITE || "NEST-LOCAL-04",
    DB: {
      prepare,
      async batch(statements) {
        db.exec("BEGIN IMMEDIATE");
        try {
          const out = [];
          for (const s of statements) out.push(await s.run());
          db.exec("COMMIT");
          return out;
        } catch (e) {
          db.exec("ROLLBACK");
          throw e;
        }
      },
    },
    close: () => db.close(),
    raw: db,
    ASSETS: {
      async fetch(request) {
        const url = new URL(request.url);
        let filename = resolve(root, "public", "." + url.pathname);
        if (
          !filename.startsWith(resolve(root, "public") + "/") &&
          !filename.startsWith(resolve(root, "public") + "\\")
        )
          filename = resolve(root, "public/index.html");
        if (!existsSync(filename) || !extname(filename))
          filename = resolve(root, "public/index.html");
        const types = {
          ".html": "text/html; charset=utf-8",
          ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8",
          ".svg": "image/svg+xml",
        };
        try {
          return new Response(readFileSync(filename), {
            headers: {
              "Content-Type":
                types[extname(filename)] || "application/octet-stream",
            },
          });
        } catch {
          return new Response("Not found", { status: 404 });
        }
      },
    },
  };
}

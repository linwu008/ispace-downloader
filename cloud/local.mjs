import { DatabaseSync } from "node:sqlite";
import {
  readFileSync,
  existsSync,
  mkdirSync,
  writeFileSync,
  unlinkSync,
} from "node:fs";
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
  db.exec(readFileSync(resolve(root, "migrations/0002_v05.sql"), "utf8"));
  db.exec(readFileSync(resolve(root, "migrations/0003_v07.sql"), "utf8"));
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
  const objectRoot =
    filename === ":memory:"
      ? null
      : resolve(dirname(filename), "archive-objects");
  const memory = new Map();
  const objectPath = (key) => {
    if (!/^[a-z]+\/[a-f0-9]+$/.test(key)) throw Error("Invalid object key");
    return resolve(objectRoot, key.replace("/", "-"));
  };
  const bucket = {
    async put(key, value) {
      const bytes =
        typeof value === "string" ? Buffer.from(value) : Buffer.from(value);
      if (objectRoot) {
        mkdirSync(objectRoot, { recursive: true });
        writeFileSync(objectPath(key), bytes);
      } else memory.set(key, bytes);
    },
    async get(key) {
      const bytes = objectRoot
        ? existsSync(objectPath(key))
          ? readFileSync(objectPath(key))
          : null
        : memory.get(key);
      return bytes
        ? { body: bytes, text: async () => bytes.toString("utf8") }
        : null;
    },
    async head(key) {
      const value = await this.get(key);
      return value ? { size: value.body.length } : null;
    },
    async delete(key) {
      if (objectRoot) {
        if (existsSync(objectPath(key))) unlinkSync(objectPath(key));
      } else memory.delete(key);
    },
  };
  return {
    ARCHIVE_BUCKET: bucket,
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
          ".svg": "image/svg+xml", ".png":"image/png", ".ico":"image/x-icon",
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

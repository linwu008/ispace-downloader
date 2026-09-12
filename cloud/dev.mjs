import { createServer } from "node:http";
import { resolve } from "node:path";
import worker from "./src/worker.js";
import { localEnv, root } from "./local.mjs";
const port = Number(process.env.PORT || 8787);
const origin = `http://127.0.0.1:${port}`;
const env = localEnv(
  process.env.COURSENEST_DB || resolve(root, "../.runtime/cloud-v04.sqlite3"),
  origin,
);
const server = createServer(async (req, res) => {
  try {
    let size = 0;
    const chunks = [];
    for await (const chunk of req) {
      size += chunk.length;
      if (size > 2_000_000) {
        res.writeHead(413);
        res.end();
        return;
      }
      chunks.push(chunk);
    }
    const request = new Request(`http://${req.headers.host}${req.url}`, {
      method: req.method,
      headers: req.headers,
      ...(["GET", "HEAD"].includes(req.method)
        ? {}
        : { body: Buffer.concat(chunks) }),
    });
    const result = await worker.fetch(request, env);
    res.writeHead(result.status, Object.fromEntries(result.headers));
    res.end(Buffer.from(await result.arrayBuffer()));
  } catch {
    res.writeHead(500);
    res.end("Local preview error");
  }
});
server.listen(port, "127.0.0.1", () =>
  console.log(
    `BNBU CourseNest v0.4 preview: ${origin} (invite: ${env.INVITE_CODE})`,
  ),
);
const timer = setInterval(
  () => worker.scheduled({}, env).catch(() => {}),
  60000,
);
timer.unref();
process.on("SIGTERM", () =>
  server.close(() => {
    env.close();
    process.exit(0);
  }),
);

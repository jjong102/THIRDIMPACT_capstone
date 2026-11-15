import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "server/robot_maps.py");
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$/;

function runMaps(args) {
  return new Promise((resolve, reject) => {
    const child = spawn("python3", [SCRIPT, ...args], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      const out = Buffer.concat(stdout);
      const err = Buffer.concat(stderr).toString("utf8").trim();
      if (code === 0) {
        resolve(out);
        return;
      }
      reject(new Error(err || "맵을 읽지 못했습니다"));
    });
  });
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Content-Length", Buffer.byteLength(body));
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

export function mapsBridgePlugin() {
  return {
    name: "maps-bridge",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = new URL(req.url ?? "/", "http://dashboard.local");
        const path = url.pathname;
        if (!path.startsWith("/api/maps")) {
          next();
          return;
        }

        if (req.method === "OPTIONS") {
          res.statusCode = 204;
          res.end();
          return;
        }

        if (req.method === "GET" && path === "/api/maps") {
          runMaps(["list"])
            .then((body) => {
              res.statusCode = 200;
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.end(body);
            })
            .catch((error) => {
              sendJson(res, 500, { ok: false, error: error.message });
            });
          return;
        }

        if (req.method === "GET" && path === "/api/maps/preview") {
          const name = url.searchParams.get("name") ?? "";
          if (!NAME_RE.test(name)) {
            sendJson(res, 400, { ok: false, error: "맵 이름이 올바르지 않습니다" });
            return;
          }
          runMaps(["png", name])
            .then((body) => {
              res.statusCode = 200;
              res.setHeader("Content-Type", "image/png");
              res.setHeader("Cache-Control", "no-cache");
              res.end(body);
            })
            .catch((error) => {
              sendJson(res, 500, { ok: false, error: error.message });
            });
          return;
        }

        if (req.method === "POST" && path === "/api/maps/view") {
          readBody(req)
            .then((raw) => {
              const body = JSON.parse(raw || "{}");
              const name = body.name;
              const degrees = Number(body.degrees);
              const zoom = Number(body.zoom);
              const centerX = Number(body.centerX ?? 50);
              const centerY = Number(body.centerY ?? 50);
              if (
                !NAME_RE.test(name ?? "") ||
                !Number.isFinite(degrees) ||
                Math.abs(degrees) > 180.0001 ||
                !Number.isFinite(zoom) ||
                zoom < 50 ||
                zoom > 250 ||
                !Number.isFinite(centerX) ||
                centerX < 0 ||
                centerX > 100 ||
                !Number.isFinite(centerY) ||
                centerY < 0 ||
                centerY > 100
              ) {
                sendJson(res, 400, { ok: false, error: "맵 보기 값이 올바르지 않습니다" });
                return null;
              }
              return runMaps([
                "view",
                name,
                String(degrees),
                String(Math.round(zoom)),
                String(centerX),
                String(centerY),
              ]);
            })
            .then((body) => {
              if (!body) return;
              res.statusCode = 200;
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.end(body);
            })
            .catch((error) => {
              sendJson(res, 500, {
                ok: false,
                error: error instanceof SyntaxError ? "요청 형식이 올바르지 않습니다" : error.message,
              });
            });
          return;
        }

        if (req.method === "POST" && path === "/api/maps/rotation") {
          readBody(req)
            .then((raw) => {
              const body = JSON.parse(raw || "{}");
              const name = body.name;
              const degrees = Number(body.degrees);
              if (
                !NAME_RE.test(name ?? "") ||
                !Number.isFinite(degrees) ||
                Math.abs(degrees) > 180.0001
              ) {
                sendJson(res, 400, { ok: false, error: "회전 값이 올바르지 않습니다" });
                return null;
              }
              return runMaps(["rotate", name, String(degrees)]);
            })
            .then((body) => {
              if (!body) return;
              res.statusCode = 200;
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.end(body);
            })
            .catch((error) => {
              sendJson(res, 500, {
                ok: false,
                error: error instanceof SyntaxError ? "요청 형식이 올바르지 않습니다" : error.message,
              });
            });
          return;
        }

        if (req.method === "POST" && path === "/api/maps/select") {
          readBody(req)
            .then((raw) => {
              const name = JSON.parse(raw || "{}").name;
              if (!NAME_RE.test(name ?? "")) {
                sendJson(res, 400, { ok: false, error: "맵 이름이 올바르지 않습니다" });
                return null;
              }
              return runMaps(["select", name]);
            })
            .then((body) => {
              if (!body) return;
              res.statusCode = 200;
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.end(body);
            })
            .catch((error) => {
              sendJson(res, 500, {
                ok: false,
                error: error instanceof SyntaxError ? "요청 형식이 올바르지 않습니다" : error.message,
              });
            });
          return;
        }

        sendJson(res, 404, { ok: false, error: "unknown maps route" });
      });
    },
  };
}

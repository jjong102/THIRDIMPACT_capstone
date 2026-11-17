import { execFileSync, spawn } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

function stopExistingBridge() {
  let listing = "";
  try {
    listing = execFileSync("ps", ["-eo", "pid,cmd"], { encoding: "utf8" });
  } catch {
    return;
  }

  for (const line of listing.split("\n")) {
    if (!line.includes("server/tts_bridge.py") && !line.includes("server\\tts_bridge.py")) {
      continue;
    }
    const pid = Number(line.trim().split(/\s+/, 1)[0]);
    if (!pid || pid === process.pid) {
      continue;
    }
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      // 이미 끝난 프로세스는 무시한다.
    }
  }
}

export function ttsBridgePlugin() {
  let bridgeProcess = null;

  function stopBridge() {
    if (bridgeProcess && !bridgeProcess.killed) {
      bridgeProcess.kill("SIGTERM");
      bridgeProcess = null;
    }
  }

  return {
    name: "tts-bridge",
    apply: "serve",
    configureServer(server) {
      const script = path.join(rootDir, "server", "tts_bridge.py");
      stopExistingBridge();

      bridgeProcess = spawn("python3", [script], {
        cwd: rootDir,
        env: process.env,
        stdio: "inherit",
      });

      bridgeProcess.on("error", (error) => {
        console.error("[tts-bridge] 시작 실패:", error.message);
      });

      server.httpServer?.on("close", stopBridge);
      process.on("SIGINT", stopBridge);
      process.on("SIGTERM", stopBridge);
    },
  };
}

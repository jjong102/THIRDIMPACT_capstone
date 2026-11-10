import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const rootDir = path.dirname(fileURLToPath(import.meta.url));
const BRIDGE_PORT = Number(process.env.MQTT_BRIDGE_PORT ?? 5175);
const RESTART_DELAY_MS = 2000;
// 다른 곳에서 띄운 브리지가 포트를 쥐고 있을 때, 그게 꺼졌는지 다시 보는 간격
const PORT_RECHECK_MS = 5000;

function isPortOpen(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port }, () => {
      socket.end();
      resolve(true);
    });
    socket.on("error", () => resolve(false));
  });
}

export function mqttBridgePlugin() {
  let bridgeProcess = null;
  let stopped = false;
  let restartTimer = null;
  let starting = false;
  let reuseLogged = false;

  function schedule(delayMs) {
    if (stopped || restartTimer) {
      return;
    }
    restartTimer = setTimeout(() => {
      restartTimer = null;
      startBridge().catch((error) => {
        console.error("[mqtt-bridge] 재시작 실패:", error.message);
      });
    }, delayMs);
  }

  function stopBridge() {
    stopped = true;
    starting = false;
    if (restartTimer) {
      clearTimeout(restartTimer);
      restartTimer = null;
    }
    if (bridgeProcess && !bridgeProcess.killed) {
      bridgeProcess.kill("SIGTERM");
    }
    bridgeProcess = null;
  }

  async function startBridge() {
    if (stopped || starting || bridgeProcess) {
      return;
    }

    // 이미 떠 있으면 재기동하지 않음 (10초 캐시 날아가며 Fresh Air가 비는 현상 방지)
    // 그 프로세스가 나중에 꺼지면 여기서 이어받아 새로 띄운다.
    if (await isPortOpen(BRIDGE_PORT)) {
      if (!reuseLogged) {
        console.log(
          `[mqtt-bridge] 이미 :${BRIDGE_PORT} 사용 중 — 기존 프로세스 재사용 (꺼지면 자동으로 새로 띄움)`
        );
        reuseLogged = true;
      }
      schedule(PORT_RECHECK_MS);
      return;
    }
    reuseLogged = false;

    starting = true;
    const script = path.join(rootDir, "server", "mqtt_bridge.py");
    const child = spawn("python3", [script], {
      cwd: rootDir,
      env: process.env,
      stdio: "inherit",
    });

    bridgeProcess = child;
    child.on("error", (error) => {
      starting = false;
      console.error("[mqtt-bridge] 시작 실패:", error.message);
      if (bridgeProcess === child) {
        bridgeProcess = null;
      }
      schedule(RESTART_DELAY_MS);
    });
    child.on("spawn", () => {
      starting = false;
    });
    child.on("exit", (code, signal) => {
      starting = false;
      if (bridgeProcess === child) {
        bridgeProcess = null;
      }
      if (stopped) {
        return;
      }
      console.error(
        `[mqtt-bridge] 종료됨 (code=${code} signal=${signal}). ${RESTART_DELAY_MS / 1000}초 후 재시작`
      );
      schedule(RESTART_DELAY_MS);
    });
  }

  return {
    name: "mqtt-bridge",
    apply: "serve",
    configureServer(server) {
      startBridge().catch((error) => {
        console.error("[mqtt-bridge] 시작 실패:", error.message);
      });
      server.httpServer?.on("close", stopBridge);
      process.on("SIGINT", stopBridge);
      process.on("SIGTERM", stopBridge);
    },
  };
}

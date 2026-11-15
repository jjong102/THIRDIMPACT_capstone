import { existsSync } from "node:fs";
import { request as httpRequest } from "node:http";
import { spawn } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";

const ALLOWED_ACTIONS = new Set(["reboot", "poweroff"]);
const ROBOT_LAN_IP = process.env.ROBOT_JETSON_HOST ?? "192.168.10.2";
const ROBOT_IDENTITY_PORT = Number(process.env.ROBOT_JETSON_IDENTITY_PORT ?? 5181);
const WIFI_IP_PREFIX = "10.96.";
const HOST_PROFILES = {
  local: {
    role: "콘솔",
    duty: "향 · 공청 · 스피커 · 마이크",
  },
  robot: {
    role: "주행",
    duty: "ROS2 자율주행",
  },
};

function parseHostnameIps(output) {
  return String(output ?? "")
    .split(/\s+/)
    .map((ip) => ip.trim())
    .filter(Boolean)
    .filter((ip) => ip !== "127.0.0.1" && ip !== "::1");
}

function runCommand(command, args, { captureStdout = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });

    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve(captureStdout ? stdout.trim() : undefined);
        return;
      }
      reject(new Error(stderr.trim() || `${command} exited ${code}`));
    });
  });
}

function readStdout(command, args) {
  return runCommand(command, args, { captureStdout: true });
}

function readHostnameIps() {
  return readStdout("hostname", ["-I"]).then(parseHostnameIps);
}

function wifiAddresses(addresses) {
  return (addresses ?? []).filter((ip) => ip.startsWith(WIFI_IP_PREFIX));
}

function wifiLevel(dbm) {
  if (typeof dbm !== "number" || Number.isNaN(dbm)) return 0;
  if (dbm >= -60) return 3;
  if (dbm >= -70) return 2;
  if (dbm >= -80) return 1;
  return 0;
}

async function readWifiStatus() {
  const empty = {
    ok: true,
    connected: false,
    iface: null,
    ssid: null,
    dbm: null,
    level: 0,
  };

  let listing = "";
  try {
    listing = await readStdout("iw", ["dev"]);
  } catch {
    return empty;
  }

  const ifaces = [];
  for (const line of listing.split("\n")) {
    const match = line.match(/Interface\s+(\S+)/);
    if (match && !match[1].startsWith("p2p")) ifaces.push(match[1]);
  }

  for (const iface of ifaces) {
    let link = "";
    try {
      link = await readStdout("iw", ["dev", iface, "link"]);
    } catch {
      continue;
    }
    if (!/Connected to/i.test(link)) continue;
    const ssid = link.match(/^\s*SSID:\s*(.+)$/m)?.[1]?.trim() || null;
    const dbmRaw = link.match(/signal:\s*(-?\d+)\s*dBm/i)?.[1];
    const dbm = dbmRaw == null ? null : Number(dbmRaw);
    return {
      ok: true,
      connected: true,
      iface,
      ssid,
      dbm,
      level: wifiLevel(dbm),
    };
  }

  return empty;
}

function fetchRobotIdentity(ip) {
  return new Promise((resolve, reject) => {
    const req = httpRequest(
      {
        host: ip,
        port: ROBOT_IDENTITY_PORT,
        path: "/",
        method: "GET",
        timeout: 1500,
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => {
          data += chunk.toString();
        });
        res.on("end", () => {
          try {
            const payload = JSON.parse(data);
            if (payload?.ok === false) {
              reject(new Error(payload.error || "identity failed"));
              return;
            }
            resolve(payload);
          } catch (error) {
            reject(error);
          }
        });
      },
    );

    req.on("timeout", () => {
      req.destroy();
      reject(new Error("timeout"));
    });
    req.on("error", reject);
    req.end();
  });
}

function parseResolvedName(output, ip) {
  const tokens = String(output ?? "")
    .split(/\s+/)
    .map((token) => token.trim())
    .filter(Boolean)
    .filter((token) => token !== ip);
  const name = tokens[0] ?? "";
  return name.replace(/\.local$/i, "") || null;
}

async function resolveLanHostname(ip) {
  try {
    const name = parseResolvedName(await readStdout("getent", ["hosts", ip]), ip);
    if (name) return name;
  } catch {
    // continue
  }

  try {
    return parseResolvedName(await readStdout("avahi-resolve", ["-a", ip]), ip);
  } catch {
    return null;
  }
}

async function isReachable(ip) {
  try {
    await runCommand("ping", ["-c", "1", "-W", "1", ip]);
    return true;
  } catch {
    return false;
  }
}

function hasSshKey() {
  const sshDir = join(homedir(), ".ssh");
  return ["id_ed25519", "id_rsa", "id_ecdsa"].some((name) =>
    existsSync(join(sshDir, name)),
  );
}

async function readRemoteViaSsh(ip) {
  const user = process.env.ROBOT_JETSON_USER;
  if (!user || !hasSshKey()) return null;

  try {
    const out = await readStdout("ssh", [
      "-o",
      "BatchMode=yes",
      "-o",
      "ConnectTimeout=2",
      "-o",
      "StrictHostKeyChecking=accept-new",
      `${user}@${ip}`,
      'printf "%s\\t%s\\t%s" "$(whoami)" "$(hostname)" "$(hostname -I)"',
    ]);
    const [who, hostname, ips] = out.split("\t");
    return {
      user: who || null,
      hostname: hostname || null,
      addresses: parseHostnameIps(ips),
    };
  } catch {
    return null;
  }
}

async function readLocalIdentity() {
  const [hostname, user, addresses] = await Promise.all([
    readStdout("hostname", []),
    readStdout("whoami", []),
    readHostnameIps(),
  ]);

  return {
    id: "local",
    ...HOST_PROFILES.local,
    online: true,
    hostname: hostname || null,
    user: user || null,
    addresses: wifiAddresses(addresses),
  };
}

async function readRemoteIdentity() {
  const ip = ROBOT_LAN_IP;
  const online = await isReachable(ip);
  if (!online) {
    return {
      id: "robot",
      ...HOST_PROFILES.robot,
      online: false,
      hostname: null,
      user: null,
      addresses: [],
      hint: "유선이 끊겨 있습니다",
    };
  }

  try {
    const payload = await fetchRobotIdentity(ip);
    const addresses = wifiAddresses(payload.addresses ?? []);
    return {
      id: "robot",
      ...HOST_PROFILES.robot,
      online: true,
      hostname: payload.hostname || (await resolveLanHostname(ip)),
      user: payload.user || null,
      addresses,
      hint: addresses.length
        ? null
        : "로봇에 10.96 Wi-Fi 주소가 없습니다",
    };
  } catch {
    const viaSsh = await readRemoteViaSsh(ip);
    if (viaSsh) {
      const addresses = wifiAddresses(viaSsh.addresses);
      return {
        id: "robot",
        ...HOST_PROFILES.robot,
        online: true,
        hostname: viaSsh.hostname,
        user: viaSsh.user,
        addresses,
        hint: addresses.length ? null : "로봇에 10.96 Wi-Fi 주소가 없습니다",
      };
    }

    return {
      id: "robot",
      ...HOST_PROFILES.robot,
      online: true,
      hostname: await resolveLanHostname(ip),
      user: null,
      addresses: [],
      hint: "로봇에서 identity를 켜면 10.96 IP가 보입니다",
    };
  }
}

async function readHosts() {
  return Promise.all([readLocalIdentity(), readRemoteIdentity()]);
}

async function runPowerAction(action) {
  const method = action === "reboot" ? "Reboot" : "PowerOff";
  const binary = action === "reboot" ? "/usr/sbin/reboot" : "/usr/sbin/poweroff";

  try {
    await runCommand("busctl", [
      "call",
      "org.freedesktop.login1",
      "/org/freedesktop/login1",
      "org.freedesktop.login1.Manager",
      method,
      "b",
      "false",
    ]);
    return;
  } catch {
    await runCommand("sudo", ["-n", binary]);
  }
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Content-Length", Buffer.byteLength(body));
  res.end(body);
}

export function systemBridgePlugin() {
  return {
    name: "system-bridge",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url?.split("?")[0] ?? "";
        if (!url.startsWith("/api/system/")) {
          next();
          return;
        }

        if (req.method === "OPTIONS") {
          res.statusCode = 204;
          res.end();
          return;
        }

        const action = url.replace("/api/system/", "");

        if (req.method === "GET" && action === "wifi") {
          readWifiStatus()
            .then((status) => {
              sendJson(res, 200, status);
            })
            .catch((error) => {
              console.error("[system-bridge] Wi-Fi 조회 실패:", error.message);
              sendJson(res, 500, {
                ok: false,
                connected: false,
                error: "Wi-Fi 상태를 읽지 못했습니다",
              });
            });
          return;
        }

        if (req.method === "GET" && (action === "ip" || action === "hosts")) {
          readHosts()
            .then((hosts) => {
              sendJson(res, 200, {
                ok: true,
                hosts,
                addresses: hosts[0]?.addresses ?? [],
              });
            })
            .catch((error) => {
              console.error("[system-bridge] 기기 정보 실패:", error.message);
              sendJson(res, 500, {
                ok: false,
                error: "기기 정보를 읽지 못했습니다",
              });
            });
          return;
        }

        if (req.method !== "POST") {
          sendJson(res, 405, { ok: false, error: "허용되지 않은 요청입니다" });
          return;
        }

        if (!ALLOWED_ACTIONS.has(action)) {
          sendJson(res, 404, { ok: false, error: "unknown action" });
          return;
        }

        runPowerAction(action)
          .then(() => {
            sendJson(res, 200, { ok: true, action });
          })
          .catch((error) => {
            console.error(`[system-bridge] ${action} 실패:`, error.message);
            sendJson(res, 500, {
              ok: false,
              error:
                "전원 권한이 없습니다. polkit 규칙을 설치한 뒤 다시 시도하세요.",
            });
          });
      });
    },
  };
}

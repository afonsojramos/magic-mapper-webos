import type {
  DiscoveryResponse,
  MapperConfig,
  MapperSettings,
  Platform,
  StatusResponse,
} from "./types";

declare global {
  interface Window {
    PalmServiceBridge?: new () => {
      call(uri: string, parameters: string): void;
      onservicecallback(raw: string): void;
    };
    magicMapperBooted?: boolean;
    magicMapperFatal?: (message: string) => void;
  }
}

export class ControllerError extends Error {
  readonly code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.name = "ControllerError";
    this.code = code;
  }
}

const APP_DIR = "/media/developer/apps/usr/palm/applications/com.github.afonsojramos.magicmapper";
const CONTROLLER = `/bin/sh ${APP_DIR}/runtime/mapperctl.sh`;

function firstErrorText(response: Record<string, unknown>): string {
  for (const candidate of [
    response.stderrString,
    response.stderr,
    response.errorText,
    response.error,
  ]) {
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  return "Magic Mapper did not return a response";
}

function parseControllerOutput<T>(response: Record<string, unknown>): T {
  const stdout = response.stdoutString ?? response.stdout ?? response.output ?? "";
  if (typeof stdout === "string") {
    const lines = stdout.trim().split("\n");
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      try {
        const parsed: unknown = JSON.parse(lines[index]);
        if (
          parsed !== null &&
          typeof parsed === "object" &&
          !Array.isArray(parsed) &&
          typeof (parsed as Record<string, unknown>).ok === "boolean"
        ) {
          return parsed as T;
        }
      } catch {
        // The Homebrew service may prefix controller output; read the last JSON line.
      }
    }
  } else if (response.returnValue !== false) {
    return response as T;
  }
  throw new ControllerError(firstErrorText(response));
}

function lunaCall(
  uri: string,
  parameters: Record<string, unknown>,
  allowErrorPayload = false,
): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    if (!window.PalmServiceBridge) {
      reject(new Error("The webOS service bridge is unavailable"));
      return;
    }
    const bridge = new window.PalmServiceBridge();
    let settled = false;
    const timeout = window.setTimeout(() => {
      if (!settled) reject(new Error("The TV service took too long to respond"));
    }, 15_000);
    bridge.onservicecallback = (raw) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      try {
        const response = JSON.parse(raw) as Record<string, unknown>;
        if (response.returnValue === false && !allowErrorPayload) {
          reject(
            new Error(
              typeof response.errorText === "string"
                ? response.errorText
                : "TV service call failed",
            ),
          );
        } else {
          resolve(response);
        }
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    };
    bridge.call(uri, JSON.stringify(parameters));
  });
}

function run<T extends { ok: boolean }>(command: string, value?: string): Promise<T> {
  const fullCommand = `${CONTROLLER} ${command}${value ? ` ${value}` : ""}`;
  return lunaCall("luna://org.webosbrew.hbchannel.service/exec", { command: fullCommand }, true)
    .then(parseControllerOutput<T>)
    .then((response) => {
      if (!response.ok) {
        const message =
          "error" in response && typeof response.error === "string" && response.error.trim()
            ? response.error
            : "Magic Mapper command failed";
        const code =
          "code" in response && typeof response.code === "string" ? response.code : undefined;
        throw new ControllerError(message, code);
      }
      return response;
    });
}

function encode(value: unknown): string {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return window.btoa(binary);
}

function mockPlatform(): Platform {
  const stored = window.localStorage.getItem("magic-mapper-demo-config");
  let config: MapperConfig = stored
    ? (JSON.parse(stored) as MapperConfig)
    : {
        netflix: "disabled",
        prime: { function: "launch_app", inputs: { app_id: "cdp-30", app_title: "Plex" } },
        alexa: "disabled",
      };
  let active = true;
  let installed = true;
  let settings: MapperSettings = {
    block_mouse: false,
    ir_exclusive_mode: true,
    ir_input_enabled: false,
  };
  const discoveries = ["rakuten", "disney", "netflix"];

  const status = (): Promise<StatusResponse> =>
    Promise.resolve({
      ok: true,
      status: {
        active,
        config,
        configDigest: "demo12345678",
        installed,
        irInputDevice: settings.ir_input_enabled ? "/dev/input/event1" : null,
        settings,
      },
    });

  return {
    isMock: true,
    status,
    install: () => {
      installed = true;
      active = true;
      return status();
    },
    start: () => {
      active = true;
      return status();
    },
    stop: () => {
      active = false;
      return status();
    },
    configure: (nextConfig) => {
      config = nextConfig;
      window.localStorage.setItem("magic-mapper-demo-config", JSON.stringify(config));
      return status();
    },
    discover: (id) => {
      window.setTimeout(() => {
        window.localStorage.setItem(`magic-mapper-discovery-${id}`, discoveries.shift() ?? "blue");
      }, 1_600);
      return Promise.resolve({ ok: true });
    },
    discoveryResult: (id): Promise<DiscoveryResponse> => {
      const button = window.localStorage.getItem(`magic-mapper-discovery-${id}`);
      return Promise.resolve(
        button ? { ok: true, button, code: 1000 } : { ok: true, pending: true },
      );
    },
    apps: () =>
      Promise.resolve({
        apps: [
          { id: "cdp-30", title: "Plex" },
          { id: "com.webos.app.browser", title: "Web Browser" },
          { id: "youtube.leanback.v4", title: "YouTube" },
          { id: "com.webos.app.hdmi1", title: "HDMI 1" },
        ],
      }),
    capabilities: () => Promise.resolve({ capabilities: { piccap: true } }),
    configureSettings: (nextSettings) => {
      settings = { ...settings, ...nextSettings };
      return status();
    },
    logs: () =>
      Promise.resolve({
        log: "Starting Magic Mapper\nEXCLUSIVE_MODE is enabled\nFirst loop complete, Magic Mapper is running",
      }),
    uninstall: () => {
      installed = false;
      active = false;
      config = {};
      return Promise.resolve({ ok: true });
    },
  };
}

export const platform: Platform = !window.PalmServiceBridge
  ? mockPlatform()
  : {
      isMock: false,
      status: () => run<StatusResponse & { ok: boolean }>("status"),
      install: () => run<StatusResponse & { ok: boolean }>("install"),
      start: () => run<StatusResponse & { ok: boolean }>("start"),
      stop: () => run<StatusResponse & { ok: boolean }>("stop"),
      configure: (config) => run<StatusResponse & { ok: boolean }>("configure", encode(config)),
      configureSettings: (settings) =>
        run<StatusResponse & { ok: boolean }>("configure-settings", encode(settings)),
      discover: (id) => run<{ ok: boolean }>("discover", id),
      discoveryResult: (id) => run<DiscoveryResponse>("discovery-result", id),
      apps: () => run<{ ok: boolean; apps: { id: string; title: string }[] }>("apps"),
      capabilities: () =>
        run<{ ok: boolean; capabilities: Record<string, boolean> }>("capabilities"),
      logs: () => run<{ ok: boolean; log: string }>("logs"),
      uninstall: () => run<{ ok: boolean }>("uninstall"),
    };

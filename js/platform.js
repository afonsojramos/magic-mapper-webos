(function () {
  "use strict";

  var APP_DIR = "/media/developer/apps/usr/palm/applications/com.github.afonsojramos.magicmapper";
  var CONTROLLER = "/usr/bin/python3 " + APP_DIR + "/runtime/mapperctl.py";

  function parseControllerOutput(response) {
    var stdout = response && (response.stdoutString || response.stdout || response.output || "");
    if (typeof stdout !== "string") return response;
    var lines = stdout.trim().split("\n");
    for (var index = lines.length - 1; index >= 0; index -= 1) {
      try { return JSON.parse(lines[index]); } catch (error) { /* keep looking */ }
    }
    throw new Error((response && (response.stderrString || response.stderr)) || "Magic Mapper did not return a response");
  }

  function lunaCall(uri, parameters) {
    return new Promise(function (resolve, reject) {
      var bridge = new window.PalmServiceBridge();
      var settled = false;
      var timeout = window.setTimeout(function () {
        if (!settled) reject(new Error("The TV service took too long to respond"));
      }, 15000);
      bridge.onservicecallback = function (raw) {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        try {
          var response = JSON.parse(raw);
          if (response.returnValue === false) reject(new Error(response.errorText || "TV service call failed"));
          else resolve(response);
        } catch (error) { reject(error); }
      };
      bridge.call(uri, JSON.stringify(parameters || {}));
    });
  }

  function run(command, value) {
    var fullCommand = CONTROLLER + " " + command;
    if (value) fullCommand += " " + value;
    return lunaCall("luna://org.webosbrew.hbchannel.service/exec", { command: fullCommand })
      .then(parseControllerOutput)
      .then(function (response) {
        if (!response.ok) throw new Error(response.error || "Magic Mapper command failed");
        return response;
      });
  }

  function loadCatalog() {
    return new Promise(function (resolve, reject) {
      var request = new XMLHttpRequest();
      request.open("GET", "runtime/action_catalog.json", true);
      request.onload = function () {
        if (request.status && (request.status < 200 || request.status >= 300)) {
          reject(new Error("Could not load the action catalogue"));
          return;
        }
        try { resolve(JSON.parse(request.responseText)); } catch (error) { reject(error); }
      };
      request.onerror = function () { reject(new Error("Could not load the action catalogue")); };
      request.send();
    });
  }

  function encode(value) {
    return window.btoa(unescape(encodeURIComponent(JSON.stringify(value))));
  }

  function mockPlatform() {
    var stored = window.localStorage.getItem("magic-mapper-demo-config");
    var config = stored ? JSON.parse(stored) : {
      netflix: "disabled",
      prime: { function: "launch_app", inputs: { app_id: "cdp-30", app_title: "Plex" } },
      alexa: "disabled"
    };
    var active = true;
    var installed = true;
    var settings = { block_mouse: false };
    var discoveries = ["rakuten", "disney", "netflix"];

    return {
      isMock: true,
      status: function () { return Promise.resolve({ status: { active: active, installed: installed, config: config, settings: settings, configDigest: "demo12345678" } }); },
      install: function () { installed = true; active = true; return this.status(); },
      start: function () { active = true; return this.status(); },
      stop: function () { active = false; return this.status(); },
      catalog: loadCatalog,
      configure: function (nextConfig) {
        config = nextConfig;
        window.localStorage.setItem("magic-mapper-demo-config", JSON.stringify(config));
        return this.status();
      },
      discover: function (id) {
        window.setTimeout(function () {
          window.localStorage.setItem("magic-mapper-discovery-" + id, discoveries.shift() || "blue");
        }, 1600);
        return Promise.resolve({ ok: true });
      },
      discoveryResult: function (id) {
        var button = window.localStorage.getItem("magic-mapper-discovery-" + id);
        return Promise.resolve(button ? { ok: true, button: button, code: 1000 } : { ok: true, pending: true });
      },
      apps: function () { return Promise.resolve({ apps: [
        { id: "cdp-30", title: "Plex" }, { id: "com.webos.app.browser", title: "Web Browser" },
        { id: "youtube.leanback.v4", title: "YouTube" }, { id: "com.webos.app.hdmi1", title: "HDMI 1" }
      ] }); },
      capabilities: function () { return Promise.resolve({ capabilities: { piccap: true } }); },
      configureSettings: function (nextSettings) {
        settings = nextSettings;
        return this.status();
      },
      logs: function () { return Promise.resolve({ log: "Starting Magic Mapper\nEXCLUSIVE_MODE is enabled\nFirst loop complete, Magic Mapper is running" }); },
      uninstall: function () { installed = false; active = false; config = {}; return Promise.resolve({ ok: true }); }
    };
  }

  if (!window.PalmServiceBridge) {
    window.MagicMapperPlatform = mockPlatform();
    return;
  }

  window.MagicMapperPlatform = {
    isMock: false,
    status: function () { return run("status"); },
    install: function () { return run("install"); },
    start: function () { return run("start"); },
    stop: function () { return run("stop"); },
    catalog: loadCatalog,
    configure: function (config) {
      return run("configure", encode(config));
    },
    configureSettings: function (settings) { return run("configure-settings", encode(settings)); },
    discover: function (id) { return run("discover", id); },
    discoveryResult: function (id) { return run("discovery-result", id); },
    apps: function () { return run("apps"); },
    capabilities: function () { return run("capabilities"); },
    logs: function () { return run("logs"); },
    uninstall: function () { return run("uninstall"); }
  };
}());

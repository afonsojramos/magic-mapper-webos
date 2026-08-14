from __future__ import print_function

import argparse
import base64
import glob
import json
import os
import signal
import subprocess
import sys
import time

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(APP_DIR, "vendor")
if VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

from magic_mapper import BUTTONS
from magic_mapper_runtime import (
    atomic_write_json,
    config_digest,
    load_action_catalog,
    validate_config,
    validate_settings,
)


APP_ID = "com.github.afonsojramos.magicmapper"
STATE_DIR = "/var/lib/webosbrew/magic-mapper"
CONFIG_PATH = os.path.join(STATE_DIR, "config.json")
SETTINGS_PATH = os.path.join(STATE_DIR, "settings.json")
PID_PATH = os.path.join(STATE_DIR, "magic-mapper.pid")
STATUS_PATH = os.path.join(STATE_DIR, "status.json")
LOG_PATH = os.path.join(STATE_DIR, "magic-mapper.log")
HOOK_PATH = "/var/lib/webosbrew/init.d/50-magic-mapper"
LEGACY_CONFIG = "/home/root/magic_mapper_config.json"
LEGACY_HOOK = "/var/lib/webosbrew/init.d/start_magic_mapper"


def result(ok=True, **values):
    values["ok"] = ok
    print(json.dumps(values, sort_keys=True))


def ensure_state():
    if not os.path.isdir(STATE_DIR):
        os.makedirs(STATE_DIR)


def python_executable():
    for candidate in ("/usr/bin/python3", "/usr/bin/python"):
        if os.path.isfile(candidate):
            return candidate
    return sys.executable


def load_config():
    with open(CONFIG_PATH) as config_file:
        return json.load(config_file)


def load_settings():
    if not os.path.isfile(SETTINGS_PATH):
        return {"block_mouse": False}
    with open(SETTINGS_PATH) as settings_file:
        settings = json.load(settings_file)
    validate_settings(settings)
    return dict({"block_mouse": False}, **settings)


def valid_functions():
    # Public functions supported by the original mapper configuration format.
    return dict((action["id"], True) for action in load_action_catalog()["actions"])


def read_pid():
    try:
        with open(PID_PATH) as pid_file:
            return int(pid_file.read().strip())
    except (IOError, OSError, ValueError):
        return None


def process_matches(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        with open("/proc/%s/cmdline" % pid, "rb") as command_file:
            command = command_file.read().decode("utf-8", "replace")
        return "managed_mapper.py" in command and APP_DIR in command
    except (IOError, OSError):
        return False


def stop_mapper():
    pid = read_pid()
    if not process_matches(pid):
        try:
            os.unlink(PID_PATH)
        except OSError:
            pass
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 4
    while time.time() < deadline:
        if not process_matches(pid):
            break
        time.sleep(0.1)
    if process_matches(pid):
        os.kill(pid, signal.SIGKILL)
    try:
        os.unlink(PID_PATH)
    except OSError:
        pass
    return True


def stop_legacy_mapper():
    for command_path in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(command_path, "rb") as command_file:
                command = command_file.read().decode("utf-8", "replace")
            if "magic_mapper.py" not in command or APP_DIR in command:
                continue
            pid = int(command_path.split("/")[2])
            os.kill(pid, signal.SIGTERM)
        except (IOError, OSError, ValueError):
            continue


def start_mapper():
    ensure_state()
    if process_matches(read_pid()):
        return read_pid(), False
    if not os.path.isfile(CONFIG_PATH):
        atomic_write_json(CONFIG_PATH, {})
    config = load_config()
    validate_config(config, BUTTONS, valid_functions())
    settings = load_settings()
    log_file = open(LOG_PATH, "ab", 0)
    command = [
        python_executable(), "-u", os.path.join(APP_DIR, "runtime", "managed_mapper.py"),
        "--config", CONFIG_PATH,
        "--state-dir", STATE_DIR,
        "--app-dir", APP_DIR,
    ]
    if settings.get("block_mouse"):
        command.append("--block-mouse")
    process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT, preexec_fn=os.setsid)
    with open(PID_PATH, "w") as pid_file:
        pid_file.write(str(process.pid))
    deadline = time.time() + 5
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Magic Mapper exited during startup; check the log")
        try:
            with open(STATUS_PATH) as status_file:
                runtime_status = json.load(status_file)
            if runtime_status.get("active") and runtime_status.get("pid") == process.pid:
                break
        except (IOError, OSError, ValueError):
            pass
        time.sleep(0.1)
    else:
        stop_mapper()
        raise RuntimeError("Magic Mapper did not become active; check the log")
    return process.pid, True


def install_hook():
    hook = """#!/bin/sh
APP_DIR=%s
STATE_DIR=%s
if [ ! -f \"$APP_DIR/runtime/mapperctl.py\" ]; then
  rm -rf \"$STATE_DIR\"
  rm -f \"$0\"
  exit 0
fi
%s \"$APP_DIR/runtime/mapperctl.py\" start >/dev/null 2>&1
""" % (APP_DIR, STATE_DIR, python_executable())
    with open(HOOK_PATH, "w") as hook_file:
        hook_file.write(hook)
    os.chmod(HOOK_PATH, 0o755)


def install():
    ensure_state()
    migrated = False
    if not os.path.isfile(CONFIG_PATH):
        if os.path.isfile(LEGACY_CONFIG):
            with open(LEGACY_CONFIG) as legacy_file:
                config = json.load(legacy_file)
            valid_buttons = set(BUTTONS.values())
            config = dict((button, action) for button, action in config.items() if button in valid_buttons)
            validate_config(config, BUTTONS, valid_functions())
            atomic_write_json(CONFIG_PATH, config)
            migrated = True
        else:
            atomic_write_json(CONFIG_PATH, {})
    if not os.path.isfile(SETTINGS_PATH):
        atomic_write_json(SETTINGS_PATH, {"block_mouse": False})
    install_hook()
    stop_legacy_mapper()
    try:
        os.unlink(LEGACY_HOOK)
    except OSError:
        pass
    pid, started = start_mapper()
    return migrated, pid, started


def status():
    pid = read_pid()
    running = process_matches(pid)
    data = {"installed": os.path.isfile(HOOK_PATH), "active": running, "pid": pid if running else None}
    try:
        with open(STATUS_PATH) as status_file:
            runtime = json.load(status_file)
        data.update(runtime)
        data["active"] = running and runtime.get("active", False)
    except (IOError, OSError, ValueError):
        pass
    if os.path.isfile(CONFIG_PATH):
        config = load_config()
        data["config"] = config
        data["configDigest"] = config_digest(config)
    else:
        data["config"] = {}
    data["settings"] = load_settings()
    return data


def decode_config(encoded):
    try:
        raw = base64.b64decode(encoded).decode("utf-8")
        config = json.loads(raw)
        validate_config(config, BUTTONS, valid_functions())
        return config
    except Exception as error:
        raise ValueError("Invalid configuration: %s" % error)


def configure(encoded):
    ensure_state()
    config = decode_config(encoded)
    atomic_write_json(CONFIG_PATH, config)
    stop_mapper()
    pid, unused_started = start_mapper()
    del unused_started
    return config, pid


def decode_settings(encoded):
    try:
        raw = base64.b64decode(encoded).decode("utf-8")
        settings = json.loads(raw)
        return validate_settings(settings)
    except Exception as error:
        raise ValueError("Invalid settings: %s" % error)


def configure_settings(encoded):
    ensure_state()
    settings = decode_settings(encoded)
    normalized = {"block_mouse": bool(settings.get("block_mouse", False))}
    atomic_write_json(SETTINGS_PATH, normalized)
    stop_mapper()
    pid, unused_started = start_mapper()
    del unused_started
    return normalized, pid


def discover(request_id):
    if not process_matches(read_pid()):
        raise RuntimeError("Start Magic Mapper before discovering a button")
    request_path = os.path.join(STATE_DIR, "discover-request.json")
    atomic_write_json(request_path, {"id": request_id, "timeout": 12})


def discovery_result(request_id):
    result_path = os.path.join(STATE_DIR, "discover-result.json")
    try:
        with open(result_path) as result_file:
            discovered = json.load(result_file)
        if discovered.get("requestId") == request_id:
            return discovered
    except (IOError, OSError, ValueError):
        pass
    return {"ok": True, "pending": True, "requestId": request_id}


def luna_json(endpoint, payload):
    process = subprocess.Popen(
        ["/usr/bin/luna-send", "-t", "1", endpoint, json.dumps(payload)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate()
    text = (stdout + b"\n" + stderr).decode("utf-8", "replace")
    for line in reversed(text.splitlines()):
        start = line.find("{")
        if start >= 0:
            try:
                return json.loads(line[start:])
            except ValueError:
                continue
    raise RuntimeError("No JSON response from %s" % endpoint)


def list_apps():
    response = luna_json("luna://com.webos.service.applicationmanager/listApps", {})
    apps = []
    for app in response.get("apps", []):
        app_id = app.get("id")
        title = app.get("title") or app.get("appDescription") or app_id
        if app_id and title and not app.get("noDisplay"):
            apps.append({"id": app_id, "title": title, "removable": bool(app.get("removable"))})
    return sorted(apps, key=lambda app: (not app.get("removable", False), app["title"].lower()))


def capabilities():
    available = False
    try:
        response = luna_json("luna://org.webosbrew.piccap.service/status", {})
        available = response.get("returnValue", True) is not False
    except Exception:
        pass
    return {"piccap": available}


def tail_log():
    try:
        with open(LOG_PATH, "rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            size = log_file.tell()
            log_file.seek(max(0, size - 12000))
            return log_file.read().decode("utf-8", "replace")[-8000:]
    except (IOError, OSError):
        return ""


def remove():
    stop_mapper()
    for path in (HOOK_PATH, LEGACY_HOOK):
        try:
            os.unlink(path)
        except OSError:
            pass
    if os.path.isdir(STATE_DIR):
        for filename in os.listdir(STATE_DIR):
            try:
                os.unlink(os.path.join(STATE_DIR, filename))
            except OSError:
                pass
        try:
            os.rmdir(STATE_DIR)
        except OSError:
            pass


def schedule_uninstall():
    remove()
    payload = json.dumps({"id": APP_ID, "subscribe": True})
    command = "sleep 1; /usr/bin/luna-send -t 1 luna://com.webos.appInstallService/dev/remove '%s' >/tmp/magic-mapper-uninstall.log 2>&1" % payload
    subprocess.Popen(["/bin/sh", "-c", command], preexec_fn=os.setsid)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=(
        "install", "status", "start", "stop", "restart", "configure", "configure-settings",
        "discover", "discovery-result", "apps", "capabilities", "logs", "remove", "uninstall",
    ))
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "install":
            migrated, pid, started = install()
            result(migrated=migrated, pid=pid, started=started, status=status())
        elif args.command == "status":
            result(status=status())
        elif args.command == "start":
            pid, started = start_mapper()
            result(pid=pid, started=started, status=status())
        elif args.command == "stop":
            result(stopped=stop_mapper(), status=status())
        elif args.command == "restart":
            stop_mapper()
            pid, unused_started = start_mapper()
            result(pid=pid, status=status())
        elif args.command == "configure":
            config, pid = configure(args.value or "")
            result(config=config, pid=pid, status=status())
        elif args.command == "configure-settings":
            settings, pid = configure_settings(args.value or "")
            result(settings=settings, pid=pid, status=status())
        elif args.command == "discover":
            discover(args.value)
            result(requestId=args.value)
        elif args.command == "discovery-result":
            print(json.dumps(discovery_result(args.value), sort_keys=True))
        elif args.command == "apps":
            result(apps=list_apps())
        elif args.command == "capabilities":
            result(capabilities=capabilities())
        elif args.command == "logs":
            result(log=tail_log())
        elif args.command == "remove":
            remove()
            result(removed=True)
        elif args.command == "uninstall":
            schedule_uninstall()
            result(uninstalling=True)
    except Exception as error:
        result(False, error=str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

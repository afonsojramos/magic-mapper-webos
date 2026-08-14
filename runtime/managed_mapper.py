from __future__ import print_function

import argparse
import fcntl
import json
import os
import select
import signal
import struct
import sys
import time


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(ROOT_DIR, "vendor")
if VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

import magic_mapper as upstream
from magic_mapper_runtime import (
    DiscoveryController,
    RUNTIME_VERSION,
    atomic_write_json,
    config_digest,
    needs_clean_back_replay,
    output_device_name,
)

with open(os.path.join(VENDOR_DIR, "upstream.json")) as upstream_file:
    UPSTREAM_METADATA = json.load(upstream_file)


CONFIG_PATH = os.path.join(ROOT_DIR, "magic_mapper_config.json")
STATE_DIR = "/tmp/magic-mapper"
APP_DIR = ROOT_DIR
STOP_REQUESTED = False


def write_input_event(output_device, event_type, code, value):
    now = time.time()
    seconds = int(now)
    microseconds = int((now - seconds) * 1000000)
    event = struct.pack("llHHi", seconds, microseconds, event_type, code, value)
    os.write(output_device, event)


def replay_clean_keypress(output_device_path, code):
    """Replace stale relayed event bytes with one fresh, complete keypress."""
    output_device = os.open(output_device_path, os.O_WRONLY)
    try:
        write_input_event(output_device, 1, code, 1)
        write_input_event(output_device, 0, 0, 0)
        time.sleep(0.08)
        write_input_event(output_device, 1, code, 0)
        write_input_event(output_device, 0, 0, 0)
    finally:
        os.close(output_device)


def load_config():
    with open(CONFIG_PATH) as config_file:
        return json.load(config_file)


def write_status(active, button_map, input_device=None, output_device=None, discovery=None, error=None):
    status = {
        "active": active,
        "pid": os.getpid() if active else None,
        "version": RUNTIME_VERSION,
        "upstreamCommit": UPSTREAM_METADATA["commit"],
        "configDigest": config_digest(button_map),
        "inputDevice": input_device,
        "outputDevice": output_device,
        "exclusive": upstream.EXCLUSIVE_MODE,
        "updatedAt": int(time.time()),
    }
    if discovery:
        status["discovery"] = discovery.state()
    if error:
        status["error"] = str(error)
    atomic_write_json(os.path.join(STATE_DIR, "status.json"), status)


def request_stop(signum, frame):
    del signum, frame
    global STOP_REQUESTED
    STOP_REQUESTED = True


def actions_for_app(actions):
    if not actions:
        return actions
    if type(actions) is not list:
        actions = [actions]
    response = upstream.luna_send("luna://com.webos.applicationManager/getForegroundAppInfo", {})
    current_app = json.loads(response).get("appId")
    filtered = []
    found_match = False
    for action in actions:
        app_id = action.get("appId")
        if app_id is None:
            filtered.append(action)
        if app_id == current_app:
            filtered.append(action)
            found_match = True
        if app_id == "!" and not found_match:
            filtered.append(action)
    return filtered


def input_loop(button_map):
    input_format = "llHHi"
    event_size = struct.calcsize(input_format)
    buttons_waiting = {}
    pressed_codes = set()
    suppress_next_sync = False
    discovery = DiscoveryController(
        os.path.join(STATE_DIR, "discover-request.json"),
        os.path.join(STATE_DIR, "discover-result.json"),
    )

    input_device_path = upstream.resolve_input_device_by_name(upstream.DEVICE_NAME)
    if not input_device_path:
        raise RuntimeError("Magic Remote input device was not found")
    print("Opening input device: %s" % input_device_path)
    input_device = open(input_device_path, "rb")
    output_device_path = None
    output_device = None

    try:
        if upstream.EXCLUSIVE_MODE:
            print("EXCLUSIVE_MODE is enabled, taking over input device")
            fcntl.ioctl(input_device, upstream.EVIOCGRAB, 1)
            passthrough_device_name = output_device_name(
                upstream.WEBOS_MAJOR_VERSION,
                upstream.OUTPUT_DEVICE_NAME,
            )
            output_device_path = upstream.resolve_input_device_by_name(passthrough_device_name)
            if not output_device_path:
                raise RuntimeError("Magic Remote output device was not found")
            print("Keys will be resent to %s: %s" % (passthrough_device_name, output_device_path))
            output_device = os.open(output_device_path, os.O_WRONLY)
        else:
            print("EXCLUSIVE_MODE is disabled, default actions cannot be blocked")

        write_status(True, button_map, input_device_path, output_device_path, discovery)
        print("Magic Mapper is running")
        last_status = 0

        while not STOP_REQUESTED:
            now = time.time()
            discovery.poll(pressed_codes, now)
            if now - last_status >= 2:
                write_status(True, button_map, input_device_path, output_device_path, discovery)
                last_status = now
            if APP_DIR and not os.path.isdir(APP_DIR):
                print("Application directory was removed, exiting")
                break

            readable, unused_write, unused_error = select.select([input_device], [], [], 0.25)
            del unused_write, unused_error
            if not readable:
                continue
            event = input_device.read(event_size)
            if len(event) != event_size:
                continue
            unused_sec, unused_usec, event_type, code, value = struct.unpack(input_format, event)
            del unused_sec, unused_usec

            if suppress_next_sync and event_type == 0:
                suppress_next_sync = False
                continue

            now = time.time()
            key = None
            if event_type == 1:
                key = upstream.BUTTONS.get(code)
                if discovery.handle_key(code, value, key, pressed_codes, now):
                    if value == 1:
                        pressed_codes.add(code)
                    elif value == 0:
                        pressed_codes.discard(code)
                    write_status(True, button_map, input_device_path, output_device_path, discovery)
                    continue
                if value == 1:
                    pressed_codes.add(code)
                elif value == 0:
                    pressed_codes.discard(code)
                discovery.poll(pressed_codes, now)
            elif event_type == 2:
                code = value
                key = upstream.MOUSE_WHEEL.get(code)
                value = 0
                buttons_waiting[code] = now

            if needs_clean_back_replay(upstream.WEBOS_MAJOR_VERSION, event_type, code):
                if value == 1:
                    suppress_next_sync = True
                if value == 0:
                    print("Replaying Back as a clean webOS 25 keypress")
                    replay_clean_keypress(output_device_path, code)
                continue

            actions = button_map.get(key)
            if actions == "disabled":
                if value == 1:
                    print("Button %s is disabled" % key)
                continue
            actions = actions_for_app(actions)

            if not actions:
                if upstream.EXCLUSIVE_MODE and not (upstream.BLOCK_MOUSE and code == 1198):
                    os.write(output_device, event)
                if key and value == 1:
                    print("Button %s is unchanged" % key)
                elif value == 1:
                    print("Button code %s ignored" % code)
                continue

            if value == 1:
                print("%s button down" % key)
                if code in buttons_waiting and now - buttons_waiting[code] < 1.0:
                    print("WARNING: Got code %s DOWN while waiting for UP" % code)
                buttons_waiting[code] = now

            if value == 0:
                if code not in buttons_waiting:
                    print("WARNING: Got code %s UP with no DOWN" % code)
                elif now - buttons_waiting[code] > 1.0:
                    print("Ignoring long press of %s" % key)
                    upstream.luna_send(
                        "luna://com.webos.notification/createToast",
                        {"sourceId": "magic mapper", "message": "long press for %s is disabled due to magic mapper" % key},
                    )
                else:
                    print("Firing action(s) for %s" % key)
                    upstream.fire_events(actions)
                buttons_waiting.pop(code, None)
    finally:
        if upstream.EXCLUSIVE_MODE:
            try:
                fcntl.ioctl(input_device, upstream.EVIOCGRAB, 0)
            except (IOError, OSError):
                pass
        input_device.close()
        if output_device is not None:
            os.close(output_device)
        write_status(False, button_map, input_device_path, output_device_path, discovery)


def main():
    global CONFIG_PATH, STATE_DIR, APP_DIR
    parser = argparse.ArgumentParser(description="Managed Magic Mapper runtime")
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument("--state-dir", default=STATE_DIR)
    parser.add_argument("--app-dir", default=APP_DIR)
    parser.add_argument("--block-mouse", action="store_true")
    parser.add_argument("--no-start-delay", action="store_true")
    args = parser.parse_args()
    CONFIG_PATH = os.path.abspath(args.config)
    STATE_DIR = os.path.abspath(args.state_dir)
    APP_DIR = os.path.abspath(args.app_dir)
    upstream.BLOCK_MOUSE = args.block_mouse

    print("Starting managed Magic Mapper")
    if not args.no_start_delay:
        time.sleep(2)
    button_map = load_config()
    upstream.WEBOS_MAJOR_VERSION = upstream.get_webos_version()
    print("WEBOS_MAJOR_VERSION: %s" % upstream.WEBOS_MAJOR_VERSION)
    print("BLOCK_MOUSE: %s" % upstream.BLOCK_MOUSE)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    input_loop(button_map)


if __name__ == "__main__":
    main()

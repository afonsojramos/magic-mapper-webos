import argparse
import contextlib
import fcntl
import json
import os
import select
import signal
import struct
import sys
import time
import traceback


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(ROOT_DIR, "vendor")
if VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

import magic_mapper as upstream
from magic_mapper_runtime import (
    RUNTIME_VERSION,
    DiscoveryController,
    atomic_write_json,
    config_digest,
)

with open(os.path.join(VENDOR_DIR, "upstream.json")) as upstream_file:
    UPSTREAM_METADATA = json.load(upstream_file)


CONFIG_PATH = os.path.join(ROOT_DIR, "magic_mapper_config.json")
STATE_DIR = "/var/lib/webosbrew/magic-mapper"
APP_DIR = ROOT_DIR
STOP_REQUESTED = False


def open_input_device(path):
    """Open an evdev node so that select() sees every queued event.

    A buffered reader drains the kernel queue into user space in one syscall but
    hands back a single event, and select() then reports the descriptor as not
    readable. The remaining events stay stranded until fresh input arrives.
    """
    return open(path, "rb", buffering=0)


def write_passthrough(output_device, output_device_path, event):
    """Forward event to the passthrough device, reopening it if the node reset.

    Returns the descriptor to use for the next write, or None when the device
    could not be reopened, in which case the following call retries the open.
    """
    if output_device is not None:
        try:
            os.write(output_device, event)
            return output_device
        except OSError as write_err:
            print(f"WARNING: passthrough write failed ({write_err}), reopening output device")
            with contextlib.suppress(OSError):
                os.close(output_device)
    try:
        reopened = os.open(output_device_path, os.O_WRONLY)
    except OSError as open_err:
        print(f"ERROR: could not reopen output device: {open_err}")
        return None
    try:
        os.write(reopened, event)
    except OSError as retry_err:
        print(f"ERROR: passthrough write failed after reopen: {retry_err}")
    return reopened


def load_config():
    with open(CONFIG_PATH) as config_file:
        return json.load(config_file)


def write_status(
    active,
    button_map,
    input_device=None,
    output_device=None,
    discovery=None,
    error=None,
    ir_input_device=None,
):
    status = {
        "active": active,
        "pid": os.getpid() if active else None,
        "version": RUNTIME_VERSION,
        "upstreamCommit": UPSTREAM_METADATA["commit"],
        "upstreamVersion": upstream.VERSION,
        "configDigest": config_digest(button_map),
        "inputDevice": input_device,
        "irInputDevice": ir_input_device,
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
    if all(action.get("appId") is None for action in actions):
        return actions
    current_app = None
    try:
        response = upstream.luna_send("luna://com.webos.applicationManager/getForegroundAppInfo", {})
        current_app = json.loads(response).get("appId")
    except Exception:
        print("ERROR: could not get foreground app, using only unconditional mappings")
        traceback.print_exc()
    filtered = []
    found_match = False
    for action in actions:
        app_id = action.get("appId")
        if app_id is None:
            filtered.append(action)
        elif current_app is not None and app_id == current_app:
            filtered.append(action)
            found_match = True
        elif current_app is not None and app_id == "!" and not found_match:
            filtered.append(action)
    return filtered


def _close_source(source):
    if source["grabbed"]:
        with contextlib.suppress(OSError):
            fcntl.ioctl(source["reader"], upstream.EVIOCGRAB, 0)
    with contextlib.suppress(OSError):
        source["reader"].close()


def _open_source(device_name, grab, required=False):
    device_path = upstream.resolve_input_device_by_name(device_name)
    if not device_path:
        if required:
            raise RuntimeError("Magic Remote input device was not found")
        print(f"WARNING: optional input device '{device_name}' was not found")
        return None
    print(f"Opening input device: {device_path} ({device_name})")
    try:
        reader = open_input_device(device_path)
    except OSError as error:
        if required:
            raise
        print(f"WARNING: could not open input device '{device_name}' ({device_path}): {error}")
        return None
    grabbed = False
    if grab:
        try:
            fcntl.ioctl(reader, upstream.EVIOCGRAB, 1)
            grabbed = True
        except OSError as error:
            if required:
                reader.close()
                raise
            print(f"WARNING: could not grab input device '{device_name}' ({device_path}): {error}")
    return {
        "name": device_name,
        "path": device_path,
        "reader": reader,
        "grabbed": grabbed,
        "required": required,
    }


def input_loop(button_map):
    input_format = "llHHi"
    event_size = struct.calcsize(input_format)
    buttons_waiting = {}
    pressed_codes = set()
    discovery = DiscoveryController(
        os.path.join(STATE_DIR, "discover-request.json"),
        os.path.join(STATE_DIR, "discover-result.json"),
    )

    sources = {}
    input_device_path = None
    ir_input_device_path = None
    output_device_path = None
    output_device = None

    try:
        primary = _open_source(upstream.INPUT_DEVICE_NAME, upstream.EXCLUSIVE_MODE, required=True)
        input_device_path = primary["path"]
        sources[primary["reader"]] = primary
        if upstream.IR_INPUT_ENABLED:
            if upstream.IR_INPUT_DEVICE_NAME == upstream.INPUT_DEVICE_NAME:
                print("IR input device matches primary input device, not opening twice")
            else:
                ir_source = _open_source(
                    upstream.IR_INPUT_DEVICE_NAME, upstream.IR_EXCLUSIVE_MODE, required=False
                )
                if ir_source:
                    sources[ir_source["reader"]] = ir_source
                    ir_input_device_path = ir_source["path"]

        if any(source["grabbed"] for source in sources.values()):
            print("Exclusive mode is enabled, taking over input device(s)")
            output_device_path = upstream.resolve_output_device()
            if not output_device_path:
                raise RuntimeError("Magic Remote output device was not found")
            print(f"Keys will be resent to: {output_device_path}")
            output_device = os.open(output_device_path, os.O_WRONLY)
        else:
            print("EXCLUSIVE_MODE is disabled, default actions cannot be blocked")

        write_status(
            True,
            button_map,
            input_device_path,
            output_device_path,
            discovery,
            ir_input_device=ir_input_device_path,
        )
        print("Magic Mapper is running")
        last_status = 0

        while not STOP_REQUESTED and sources:
            now = time.time()
            discovery.poll(pressed_codes, now)
            if now - last_status >= 2:
                write_status(
                    True,
                    button_map,
                    input_device_path,
                    output_device_path,
                    discovery,
                    ir_input_device=ir_input_device_path,
                )
                last_status = now
            if APP_DIR and not os.path.isdir(APP_DIR):
                print("Application directory was removed, exiting")
                break

            readable, unused_write, unused_error = select.select(list(sources), [], [], 0.25)
            del unused_write, unused_error
            if not readable:
                continue
            lost_primary = False
            for reader in readable:
                source = sources.get(reader)
                if not source:
                    continue
                try:
                    event = reader.read(event_size)
                except OSError as error:
                    print(f"WARNING: could not read input device '{source['name']}': {error}")
                    event = b""
                if not event:
                    if source["required"]:
                        print(f"ERROR: lost primary input device '{source['name']}', exiting")
                        lost_primary = True
                        break
                    print(f"WARNING: lost optional input device '{source['name']}', detaching it")
                    _close_source(source)
                    sources.pop(reader, None)
                    if source["path"] == ir_input_device_path:
                        ir_input_device_path = None
                    for key in [k for k in buttons_waiting if k[0] == source["name"]]:
                        buttons_waiting.pop(key, None)
                    pressed_codes = {k for k in pressed_codes if k[0] != source["name"]}
                    write_status(
                        True,
                        button_map,
                        input_device_path,
                        output_device_path,
                        discovery,
                        ir_input_device=ir_input_device_path,
                    )
                    continue
                if len(event) != event_size:
                    continue
                unused_sec, unused_usec, event_type, code, value = struct.unpack(input_format, event)
                del unused_sec, unused_usec

                now = time.time()
                key = None
                wait_key = (source["name"], code)
                if event_type == 1:
                    key = upstream.BUTTONS.get(code)
                    if discovery.handle_key(code, value, key, pressed_codes, now):
                        if value == 1:
                            pressed_codes.add(wait_key)
                        elif value == 0:
                            pressed_codes.discard(wait_key)
                        write_status(
                            True,
                            button_map,
                            input_device_path,
                            output_device_path,
                            discovery,
                            ir_input_device=ir_input_device_path,
                        )
                        continue
                    if value == 1:
                        pressed_codes.add(wait_key)
                    elif value == 0:
                        pressed_codes.discard(wait_key)
                    discovery.poll(pressed_codes, now)
                elif event_type == 2:
                    code = value
                    key = upstream.MOUSE_WHEEL.get(code)
                    value = 0
                    wait_key = (source["name"], code)
                    buttons_waiting[wait_key] = now

                actions = button_map.get(key)
                if actions == "disabled":
                    if value == 1:
                        print(f"Button {key} is disabled")
                    continue
                actions = actions_for_app(actions)

                if not actions:
                    if source["grabbed"] and not (upstream.BLOCK_MOUSE and code == 1198):
                        output_device = write_passthrough(output_device, output_device_path, event)
                    if key and value == 1:
                        print(f"Button {key} is unchanged")
                    elif value == 1:
                        print(f"Button code {code} ignored")
                    continue

                if value == 1:
                    print(f"{key} button down")
                    if wait_key in buttons_waiting and now - buttons_waiting[wait_key] < 1.0:
                        print(f"WARNING: Got code {code} DOWN while waiting for UP")
                    buttons_waiting[wait_key] = now

                if value == 0:
                    if wait_key not in buttons_waiting:
                        print(f"WARNING: Got code {code} UP with no DOWN")
                    elif now - buttons_waiting[wait_key] > 1.0:
                        print(f"Ignoring long press of {key}")
                        try:
                            upstream.luna_send(
                                "luna://com.webos.notification/createToast",
                                {
                                    "sourceId": "magic mapper",
                                    "message": f"long press for {key} is disabled due to magic mapper",
                                },
                            )
                        except Exception:
                            traceback.print_exc()
                    else:
                        print(f"Firing action(s) for {key}")
                        try:
                            upstream.fire_events(actions)
                        except Exception:
                            print(f"ERROR: action for button {key} failed")
                            traceback.print_exc()
                    buttons_waiting.pop(wait_key, None)
            if lost_primary:
                break
    finally:
        for source in list(sources.values()):
            _close_source(source)
        if output_device is not None:
            with contextlib.suppress(OSError):
                os.close(output_device)
        write_status(
            False,
            button_map,
            input_device_path,
            output_device_path,
            discovery,
            ir_input_device=ir_input_device_path,
        )


def main():
    global CONFIG_PATH, STATE_DIR, APP_DIR
    parser = argparse.ArgumentParser(description="Managed Magic Mapper runtime")
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument("--state-dir", default=STATE_DIR)
    parser.add_argument("--app-dir", default=APP_DIR)
    parser.add_argument("--block-mouse", action="store_true")
    parser.add_argument("--ir-input", action="store_true")
    parser.add_argument("--no-ir-exclusive", action="store_true")
    parser.add_argument("--no-start-delay", action="store_true")
    args = parser.parse_args()
    CONFIG_PATH = os.path.abspath(args.config)
    STATE_DIR = os.path.abspath(args.state_dir)
    APP_DIR = os.path.abspath(args.app_dir)
    upstream.BLOCK_MOUSE = args.block_mouse
    upstream.IR_INPUT_ENABLED = args.ir_input
    upstream.IR_EXCLUSIVE_MODE = not args.no_ir_exclusive

    print(f"Starting managed Magic Mapper (upstream {upstream.VERSION})")
    if not args.no_start_delay:
        time.sleep(2)
    button_map = load_config()
    upstream.WEBOS_MAJOR_VERSION = upstream.get_webos_version()
    print(f"WEBOS_MAJOR_VERSION: {upstream.WEBOS_MAJOR_VERSION}")
    print(f"BLOCK_MOUSE: {upstream.BLOCK_MOUSE}")
    print(f"IR_INPUT_ENABLED: {upstream.IR_INPUT_ENABLED}")
    print(f"IR_EXCLUSIVE_MODE: {upstream.IR_EXCLUSIVE_MODE}")
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    input_loop(button_map)


if __name__ == "__main__":
    main()

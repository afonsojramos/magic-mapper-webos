from __future__ import print_function

import hashlib
import json
import os
import tempfile
import time


RUNTIME_VERSION = "0.2.0"
WEBOS_25_OUTPUT_DEVICE_NAME = "LGE M-RCU - Builtin [1]"
WEBOS_25_BACK_CODE = 412
ACTION_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "action_catalog.json")


def output_device_name(webos_major_version, legacy_name):
    """Choose the passthrough device that preserves Magic Remote semantics."""
    # webOS 25 interprets Back from Builtin [2] as Exit; Builtin [1] preserves it.
    if webos_major_version >= 10:
        return WEBOS_25_OUTPUT_DEVICE_NAME
    return legacy_name


def needs_clean_back_replay(webos_major_version, event_type, code):
    return webos_major_version >= 10 and event_type == 1 and code == WEBOS_25_BACK_CODE


def atomic_write_json(path, value):
    """Write JSON without ever exposing a partially-written state file."""
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    handle, temporary_path = tempfile.mkstemp(prefix=".magic-mapper-", dir=directory or None)
    try:
        with os.fdopen(handle, "w") as temporary_file:
            json.dump(value, temporary_file, sort_keys=True)
            temporary_file.write("\n")
        os.rename(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def config_digest(config):
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def load_action_catalog(path=None):
    with open(path or ACTION_CATALOG_PATH) as catalog_file:
        catalog = json.load(catalog_file)
    if not isinstance(catalog.get("actions"), list) or not isinstance(catalog.get("categories"), list):
        raise ValueError("Action catalog must contain categories and actions")
    return catalog


def _validate_input(button, function_name, field, value, valid_buttons):
    field_name = field["name"]
    field_type = field["type"]
    prefix = "%s.%s.%s" % (button, function_name, field_name)

    if field_type in ("string", "url"):
        if not isinstance(value, str):
            raise ValueError("%s must be text" % prefix)
        if not value and not field.get("allowEmpty"):
            raise ValueError("%s must not be empty" % prefix)
        if field_type == "url" and not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("%s must use http:// or https://" % prefix)
    elif field_type == "stringList":
        values = value if isinstance(value, list) else [value]
        if not all(isinstance(item, str) and item for item in values):
            raise ValueError("%s must contain non-empty text values" % prefix)
    elif field_type == "object":
        if not isinstance(value, dict):
            raise ValueError("%s must be a JSON object" % prefix)
    elif field_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("%s must be true or false" % prefix)
    elif field_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("%s must be a whole number" % prefix)
    elif field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("%s must be a number" % prefix)
    elif field_type == "choice":
        allowed = [option["value"] for option in field.get("options", [])]
        if value not in allowed:
            raise ValueError("%s must be one of: %s" % (prefix, ", ".join(allowed)))
    elif field_type == "button":
        if value not in valid_buttons:
            raise ValueError("Remap action for %s needs a valid target button" % button)
    else:
        raise ValueError("Unknown field type for %s: %s" % (prefix, field_type))

    if field_type in ("integer", "number"):
        if "min" in field and value < field["min"]:
            raise ValueError("%s must be at least %s" % (prefix, field["min"]))
        if "max" in field and value > field["max"]:
            raise ValueError("%s must be at most %s" % (prefix, field["max"]))


def validate_config(config, buttons, functions=None, catalog=None):
    """Validate the configuration shape used by the app before activating it."""
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")

    valid_buttons = set(buttons.values())
    catalog = catalog or load_action_catalog()
    schemas = dict((action["id"], action) for action in catalog["actions"])
    if functions is not None:
        schemas = dict((name, schema) for name, schema in schemas.items() if name in functions or name == "disabled")
    for button, actions in config.items():
        if button not in valid_buttons:
            raise ValueError("Unknown button: %s" % button)
        if actions == "disabled":
            continue
        if not isinstance(actions, list):
            actions = [actions]
        if not actions:
            raise ValueError("%s must have at least one action" % button)
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError("Invalid action for %s" % button)
            function_name = action.get("function")
            if function_name not in schemas:
                raise ValueError("Unknown function for %s: %s" % (button, function_name))
            unknown_action_keys = set(action) - set(("function", "inputs", "appId"))
            if unknown_action_keys:
                raise ValueError("Unknown action property for %s: %s" % (button, sorted(unknown_action_keys)[0]))
            if "appId" in action and not isinstance(action["appId"], str):
                raise ValueError("App condition for %s must be text" % button)
            inputs = action.get("inputs", {})
            if not isinstance(inputs, dict):
                raise ValueError("Inputs for %s must be an object" % button)
            schema = schemas[function_name]
            fields = dict((field["name"], field) for field in schema.get("inputs", []))
            unknown_inputs = set(inputs) - set(fields)
            if unknown_inputs:
                raise ValueError("Unknown input for %s: %s" % (button, sorted(unknown_inputs)[0]))
            for field_name, field in fields.items():
                if field.get("required") and field_name not in inputs:
                    if function_name == "launch_app" and field_name == "app_id":
                        raise ValueError("Launch action for %s needs an app" % button)
                    raise ValueError("%s.%s needs %s" % (button, function_name, field_name))
                if field_name in inputs:
                    _validate_input(button, function_name, field, inputs[field_name], valid_buttons)
    return config


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("Settings must be a JSON object")
    unknown = set(settings) - set(("block_mouse",))
    if unknown:
        raise ValueError("Unknown setting: %s" % sorted(unknown)[0])
    if "block_mouse" in settings and not isinstance(settings["block_mouse"], bool):
        raise ValueError("block_mouse must be true or false")
    return settings


class DiscoveryController(object):
    """Coordinates a one-shot, suppressed remote-button discovery request."""

    def __init__(self, request_path, result_path, settle_seconds=0.25, cancel_codes=None):
        self.request_path = request_path
        self.result_path = result_path
        self.settle_seconds = settle_seconds
        self.cancel_codes = set(cancel_codes or (412,))
        self.request_id = None
        self.phase = "idle"
        self.deadline = 0
        self.armed_at = 0
        self.candidate = None
        self.suppressed_until_release = set()

    def poll(self, pressed_codes, now=None):
        now = now if now is not None else time.time()
        self._load_request(now)
        if self.phase == "waiting_for_release" and not pressed_codes:
            self.phase = "settling"
            self.armed_at = now + self.settle_seconds
        if self.phase == "settling" and now >= self.armed_at:
            self.phase = "armed"
        if self.phase not in ("idle", "complete", "timed_out") and now >= self.deadline:
            self.phase = "timed_out"
            self._write_result({"ok": False, "error": "timeout"})
        return self.phase

    def handle_key(self, code, value, name, pressed_codes, now=None):
        """Return True when the event belongs to discovery and must be suppressed."""
        now = now if now is not None else time.time()
        self.poll(pressed_codes, now)
        if code in self.suppressed_until_release:
            if value == 0:
                self.suppressed_until_release.discard(code)
            return True
        if self.phase not in ("armed", "capturing"):
            return False

        if self.phase == "armed" and value == 1 and code in self.cancel_codes:
            self.suppressed_until_release.add(code)
            self.phase = "complete"
            self._write_result({"ok": False, "error": "cancelled"})
            return True

        if self.phase == "armed" and value == 1:
            self.phase = "capturing"
            self.candidate = code
            return True

        if self.phase == "capturing" and code == self.candidate:
            if value == 0:
                self.phase = "complete"
                self._write_result({
                    "ok": True,
                    "button": name or "code_%s" % code,
                    "code": code,
                })
            return True
        return False

    def state(self):
        return {
            "requestId": self.request_id,
            "phase": self.phase,
        }

    def _load_request(self, now):
        try:
            with open(self.request_path) as request_file:
                request = json.load(request_file)
        except (IOError, OSError, ValueError):
            return
        request_id = request.get("id")
        if not request_id or request_id == self.request_id:
            return
        self.request_id = request_id
        self.phase = "waiting_for_release"
        self.candidate = None
        timeout = min(max(float(request.get("timeout", 12)), 3), 30)
        self.deadline = now + timeout
        try:
            os.unlink(self.result_path)
        except OSError:
            pass

    def _write_result(self, result):
        result.update({
            "requestId": self.request_id,
            "completedAt": int(time.time()),
        })
        atomic_write_json(self.result_path, result)

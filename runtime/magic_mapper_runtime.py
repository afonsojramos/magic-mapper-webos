import contextlib
import hashlib
import json
import os
import tempfile
import time


RUNTIME_VERSION = "0.3.0"
ACTION_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "action_catalog.json")


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
    prefix = f"{button}.{function_name}.{field_name}"

    if field_type in ("string", "url"):
        if not isinstance(value, str):
            raise ValueError(f"{prefix} must be text")
        if not value and not field.get("allowEmpty"):
            raise ValueError(f"{prefix} must not be empty")
        if field_type == "url" and not value.startswith(("http://", "https://")):
            raise ValueError(f"{prefix} must use http:// or https://")
    elif field_type == "stringList":
        values = value if isinstance(value, list) else [value]
        if not all(isinstance(item, str) and item for item in values):
            raise ValueError(f"{prefix} must contain non-empty text values")
    elif field_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{prefix} must be a JSON object")
    elif field_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{prefix} must be true or false")
    elif field_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{prefix} must be a whole number")
    elif field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{prefix} must be a number")
    elif field_type == "choice":
        allowed = [option["value"] for option in field.get("options", [])]
        if value not in allowed:
            raise ValueError(f"{prefix} must be one of: {', '.join(allowed)}")
    elif field_type == "button":
        if value not in valid_buttons:
            raise ValueError(f"Remap action for {button} needs a valid target button")
    else:
        raise ValueError(f"Unknown field type for {prefix}: {field_type}")

    if field_type in ("integer", "number"):
        if "min" in field and value < field["min"]:
            raise ValueError(f"{prefix} must be at least {field['min']}")
        if "max" in field and value > field["max"]:
            raise ValueError(f"{prefix} must be at most {field['max']}")


def validate_config(config, buttons, functions=None, catalog=None):
    """Validate the configuration shape used by the app before activating it."""
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")

    valid_buttons = set(buttons.values())
    catalog = catalog or load_action_catalog()
    schemas = {action["id"]: action for action in catalog["actions"]}
    if functions is not None:
        schemas = {name: schema for name, schema in schemas.items() if name in functions or name == "disabled"}
    for button, actions in config.items():
        if button not in valid_buttons:
            raise ValueError(f"Unknown button: {button}")
        if actions == "disabled":
            continue
        if not isinstance(actions, list):
            actions = [actions]
        if not actions:
            raise ValueError(f"{button} must have at least one action")
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError(f"Invalid action for {button}")
            function_name = action.get("function")
            if function_name not in schemas:
                raise ValueError(f"Unknown function for {button}: {function_name}")
            unknown_action_keys = set(action) - {"function", "inputs", "appId"}
            if unknown_action_keys:
                raise ValueError(f"Unknown action property for {button}: {sorted(unknown_action_keys)[0]}")
            if "appId" in action and not isinstance(action["appId"], str):
                raise ValueError(f"App condition for {button} must be text")
            inputs = action.get("inputs", {})
            if not isinstance(inputs, dict):
                raise ValueError(f"Inputs for {button} must be an object")
            schema = schemas[function_name]
            fields = {field["name"]: field for field in schema.get("inputs", [])}
            unknown_inputs = set(inputs) - set(fields)
            if unknown_inputs:
                raise ValueError(f"Unknown input for {button}: {sorted(unknown_inputs)[0]}")
            for field_name, field in fields.items():
                if field.get("required") and field_name not in inputs:
                    if function_name == "launch_app" and field_name == "app_id":
                        raise ValueError(f"Launch action for {button} needs an app")
                    raise ValueError(f"{button}.{function_name} needs {field_name}")
                if field_name in inputs:
                    _validate_input(button, function_name, field, inputs[field_name], valid_buttons)
    return config


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("Settings must be a JSON object")
    unknown = set(settings) - {"block_mouse"}
    if unknown:
        raise ValueError(f"Unknown setting: {sorted(unknown)[0]}")
    if "block_mouse" in settings and not isinstance(settings["block_mouse"], bool):
        raise ValueError("block_mouse must be true or false")
    return settings


class DiscoveryController:
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
                    "button": name or f"code_{code}",
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
        except (OSError, ValueError):
            return
        request_id = request.get("id")
        if not request_id or request_id == self.request_id:
            return
        self.request_id = request_id
        self.phase = "waiting_for_release"
        self.candidate = None
        timeout = min(max(float(request.get("timeout", 12)), 3), 30)
        self.deadline = now + timeout
        with contextlib.suppress(OSError):
            os.unlink(self.result_path)

    def _write_result(self, result):
        result.update({
            "requestId": self.request_id,
            "completedAt": int(time.time()),
        })
        atomic_write_json(self.result_path, result)

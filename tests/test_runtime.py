import contextlib
import hashlib
import json
import os
import select
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "runtime"))

import managed_mapper
from magic_mapper_runtime import (
    DiscoveryController,
    config_digest,
    load_action_catalog,
    validate_config,
    validate_settings,
)
from managed_mapper import open_input_device, write_passthrough


BUTTONS = {1: "netflix", 2: "prime", 3: "ok"}
FUNCTIONS = {"launch_app": True, "press_button": True}


class ConfigValidationTests(unittest.TestCase):
    def test_catalog_covers_supported_upstream_actions(self):
        catalog = load_action_catalog()
        self.assertEqual(
            {action["id"] for action in catalog["actions"]},
            set(managed_mapper.upstream.CONFIG_FUNCTIONS),
        )

    def test_accepts_representative_inputs_for_every_action(self):
        catalog = load_action_catalog()
        functions = {action["id"]: True for action in catalog["actions"]}
        actions = [
            {"function": "cycle_energy_mode", "inputs": {"reverse_order": True, "notifications": True}},
            {"function": "toggle_eye_comfort", "inputs": {"notifications": False}},
            {"function": "screen_off", "inputs": {}},
            {"function": "set_energy_mode", "inputs": {"mode": "med", "notifications": True}},
            {"function": "increase_oled_light", "inputs": {"increment": 5, "disable_energy_savings": True}},
            {"function": "reduce_oled_light", "inputs": {"increment": 15, "notifications": False}},
            {"function": "set_oled_backlight", "inputs": {"backlight": 65}},
            {"function": "launch_app", "inputs": {"app_id": "cdp-30", "app_title": "Plex", "params": {"target": "library"}}},
            {"function": "send_ir_command", "inputs": {"tv_input": "OPTICAL", "keycode": "IR_KEY_POWER", "device_type": "audio"}},
            {"function": "curl", "inputs": {"url": "https://example.com/hook", "method": "POST", "headers": ["X-Test: yes"], "data": "{}"}},
            {"function": "press_button", "inputs": {"button": "ok"}},
            {"function": "send_cec_button", "inputs": {"code": 18882561}},
            {"function": "set_dynamic_tone_mapping", "inputs": {"value": "HGIG"}},
            {"function": "disabled", "inputs": {}},
            {"function": "send_tcp_command", "inputs": {"ip": "192.168.1.50", "port": 23, "command": "POWER ON", "timeout": 2.5}},
            {"function": "toggle_piccap", "inputs": {}},
            {"function": "toggle_bluetooth", "inputs": {"notifications": True}},
        ]
        config = {"netflix": actions}
        self.assertIs(validate_config(config, BUTTONS, functions), config)

    def test_rejects_invalid_action_inputs(self):
        catalog = load_action_catalog()
        functions = {action["id"]: True for action in catalog["actions"]}
        invalid_actions = [
            {"function": "set_oled_backlight", "inputs": {"backlight": 101}},
            {"function": "set_energy_mode", "inputs": {"mode": "turbo"}},
            {"function": "curl", "inputs": {"url": "ftp://example.com/file"}},
            {"function": "send_tcp_command", "inputs": {"ip": "tv", "port": 70000, "command": "on"}},
            {"function": "send_cec_button", "inputs": {"code": 1.5}},
            {"function": "launch_app", "inputs": {"app_id": "cdp-30", "params": []}},
            {"function": "screen_off", "inputs": {"surprise": True}},
        ]
        for action in invalid_actions:
            with self.subTest(action=action), self.assertRaises(ValueError):
                validate_config({"netflix": action}, BUTTONS, functions)

    def test_validates_global_settings(self):
        settings = {"block_mouse": True, "ir_input_enabled": True, "ir_exclusive_mode": False}
        self.assertIs(validate_settings(settings), settings)
        with self.assertRaisesRegex(ValueError, "true or false"):
            validate_settings({"block_mouse": "yes"})
        with self.assertRaisesRegex(ValueError, "true or false"):
            validate_settings({"ir_input_enabled": 1})
        with self.assertRaisesRegex(ValueError, "Unknown setting"):
            validate_settings({"block_mouse": False, "other": True})

    def test_webos_delivers_back_button_events_to_the_app(self):
        app_info = json.loads((Path(__file__).parents[1] / "appinfo.json").read_text())
        self.assertIs(app_info.get("disableBackHistoryAPI"), True)

    def test_vendored_upstream_matches_pin(self):
        with open(os.path.join(ROOT_DIR, "vendor", "upstream.json")) as metadata_file:
            metadata = json.load(metadata_file)
        with open(os.path.join(ROOT_DIR, "vendor", "magic_mapper.py"), "rb") as source_file:
            digest = hashlib.sha256(source_file.read()).hexdigest()
        self.assertEqual(digest, metadata["sha256"])

    def test_accepts_supported_app_actions(self):
        config = {
            "netflix": "disabled",
            "prime": {"function": "launch_app", "inputs": {"app_id": "cdp-30"}},
        }
        self.assertIs(validate_config(config, BUTTONS, FUNCTIONS), config)

    def test_rejects_unknown_button(self):
        with self.assertRaisesRegex(ValueError, "Unknown button"):
            validate_config({"mystery": "disabled"}, BUTTONS, FUNCTIONS)

    def test_rejects_incomplete_remap(self):
        with self.assertRaisesRegex(ValueError, "valid target"):
            validate_config(
                {"prime": {"function": "press_button", "inputs": {"button": "missing"}}},
                BUTTONS,
                FUNCTIONS,
            )

    def test_digest_is_stable_across_key_order(self):
        self.assertEqual(config_digest({"a": 1, "b": 2}), config_digest({"b": 2, "a": 1}))


class UpstreamCompatibilityTests(unittest.TestCase):
    def test_loads_upstream_1_1_0(self):
        self.assertEqual(managed_mapper.upstream.VERSION, "1.1.0")
        self.assertEqual(managed_mapper.upstream.VERSION, managed_mapper.UPSTREAM_METADATA["version"])

    def test_catalog_actions_are_callable_upstream_functions(self):
        for action in load_action_catalog()["actions"]:
            with self.subTest(action=action["id"]):
                self.assertIn(action["id"], managed_mapper.upstream.CONFIG_FUNCTIONS)
                self.assertTrue(callable(getattr(managed_mapper.upstream, action["id"])))

    def test_webos_25_uses_upstream_passthrough_device_that_preserves_back(self):
        self.assert_output_device(10, "/dev/input/event4")

    def test_older_webos_keeps_upstream_passthrough_device(self):
        self.assert_output_device(9, "/dev/input/event5")

    def assert_output_device(self, version, expected):
        devices = [
            ("LGE M-RCU - Builtin [0]", "/dev/input/event3"),
            ("LGE M-RCU - Builtin [1]", "/dev/input/event4"),
            ("LGE M-RCU - Builtin [2]", "/dev/input/event5"),
        ]
        with mock.patch.object(managed_mapper.upstream, "WEBOS_MAJOR_VERSION", version), \
                mock.patch.object(managed_mapper.upstream, "read_input_devices", return_value=devices):
            self.assertEqual(managed_mapper.upstream.resolve_output_device(), expected)

    def back_events(self, values=(1, 0)):
        return b"".join(
            struct.pack("llHHi", 1000 + index, 123456, event_type, code, event_value)
            for index, value in enumerate(values)
            for event_type, code, event_value in [(1, 412, value), (0, 0, 0)]
        )

    def run_loop(self, config, devices, exclusive=True, events=None, discover=False):
        with tempfile.TemporaryDirectory() as directory:
            input_path = os.path.join(directory, "input")
            output_path = os.path.join(directory, "output")
            if events is None:
                events = self.back_events()
            Path(input_path).write_bytes(events)
            Path(output_path).touch()
            device_paths = [(name, input_path if source else output_path) for name, source in devices]
            request_path = Path(directory, "discover-request.json")
            if discover:
                request_path.write_text(json.dumps({"id": "back-test", "timeout": 12}))

            def readable(readers, writers, errors, timeout):
                if readers[0].tell() == len(events):
                    managed_mapper.STOP_REQUESTED = True
                    return [], [], []
                return readers, [], []

            with mock.patch.multiple(managed_mapper, STATE_DIR=directory, APP_DIR=directory, STOP_REQUESTED=False), \
                    mock.patch.multiple(managed_mapper.upstream, WEBOS_MAJOR_VERSION=10, EXCLUSIVE_MODE=exclusive), \
                    mock.patch.object(managed_mapper.upstream, "read_input_devices", return_value=device_paths), \
                    mock.patch.object(managed_mapper.fcntl, "ioctl"), \
                    mock.patch.object(managed_mapper.select, "select", side_effect=readable), \
                    mock.patch.object(managed_mapper.upstream, "luna_send", return_value='{"appId":"test-app"}'), \
                    mock.patch.object(managed_mapper.upstream, "fire_events") as fire_events:
                if discover:
                    discovery = DiscoveryController(str(request_path), os.path.join(directory, "discover-result.json"), settle_seconds=0)
                    with mock.patch.object(managed_mapper, "DiscoveryController", return_value=discovery):
                        managed_mapper.input_loop(config)
                else:
                    managed_mapper.input_loop(config)
                status = json.loads(Path(directory, "status.json").read_text())
                output = Path(output_path).read_bytes()
                actions = fire_events.call_args_list
            return output, status, actions

    def test_unmapped_back_is_forwarded_byte_for_byte(self):
        events = self.back_events()
        output, _, _ = self.run_loop({}, [
            ("LGE M-RCU - Builtin [0]", True),
            ("LGE M-RCU - Builtin [1]", False),
        ], events=events)
        self.assertEqual(output, events)

    def test_back_keydown_is_forwarded_before_release(self):
        events = self.back_events((1,))
        output, _, _ = self.run_loop({}, [
            ("LGE M-RCU - Builtin [0]", True),
            ("LGE M-RCU - Builtin [1]", False),
        ], events=events)
        self.assertEqual(output, events)

    def test_back_repeat_preserves_original_events_and_timestamps(self):
        events = self.back_events((1, 2, 2, 0))
        output, _, _ = self.run_loop({}, [
            ("LGE M-RCU - Builtin [0]", True),
            ("LGE M-RCU - Builtin [1]", False),
        ], events=events)
        self.assertEqual(output, events)

    def test_disabled_back_does_not_forward_key_events(self):
        output, _, actions = self.run_loop({"back": "disabled"}, [
            ("LGE M-RCU - Builtin [0]", True),
            ("LGE M-RCU - Builtin [1]", False),
        ])
        size = struct.calcsize("llHHi")
        forwarded = [struct.unpack("llHHi", output[offset:offset + size])[2:] for offset in range(0, len(output), size)]
        self.assertTrue(all(event_type != 1 for event_type, _, _ in forwarded))
        self.assertEqual(actions, [])

    def test_discovery_cancel_does_not_forward_back_key_events(self):
        output, status, actions = self.run_loop({}, [
            ("LGE M-RCU - Builtin [0]", True),
            ("LGE M-RCU - Builtin [1]", False),
        ], discover=True)
        size = struct.calcsize("llHHi")
        forwarded = [struct.unpack("llHHi", output[offset:offset + size])[2:] for offset in range(0, len(output), size)]
        self.assertTrue(all(event_type != 1 for event_type, _, _ in forwarded))
        self.assertEqual(status["discovery"]["phase"], "complete")
        self.assertEqual(actions, [])

    def test_managed_loop_uses_upstream_output_fallback(self):
        output, status, _ = self.run_loop({}, [
            ("LGE M-RCU - Builtin [0]", True),
            ("LGE M-RCU - Builtin [2]", False),
        ])
        self.assertTrue(output)
        self.assertIsNotNone(status["outputDevice"])
        self.assertFalse(status["active"])
        self.assertEqual(status["upstreamVersion"], "1.1.0")

    def test_back_mapping_is_not_bypassed_by_passthrough(self):
        action = {"function": "disabled"}
        _, _, actions = self.run_loop({"back": action}, [
            ("LGE M-RCU - Builtin [0]", True),
            ("LGE M-RCU - Builtin [1]", False),
        ])
        self.assertEqual(actions, [mock.call([action])])

    def test_nonexclusive_back_does_not_open_a_passthrough_device(self):
        output, status, _ = self.run_loop({}, [("LGE M-RCU - Builtin [0]", True)], exclusive=False)
        self.assertEqual(output, b"")
        self.assertIsNone(status["outputDevice"])

    def test_ir_input_reads_from_ir_receiver_and_records_status(self):
        with tempfile.TemporaryDirectory() as directory:
            bt_path = os.path.join(directory, "bt")
            ir_path = os.path.join(directory, "ir")
            output_path = os.path.join(directory, "output")
            ir_events = self.back_events()
            Path(bt_path).write_bytes(b"")
            Path(ir_path).write_bytes(ir_events)
            Path(output_path).touch()
            device_paths = [
                ("LGE M-RCU - Builtin [0]", bt_path),
                ("LGE RCU", ir_path),
                ("LGE M-RCU - Builtin [1]", output_path),
            ]
            action = {"function": "toggle_bluetooth", "inputs": {"notifications": True}}

            def readable(readers, writers, errors, timeout):
                del writers, errors, timeout
                ir_reader = next(reader for reader in readers if reader.name == ir_path)
                if ir_reader.tell() == len(ir_events):
                    managed_mapper.STOP_REQUESTED = True
                    return [], [], []
                return [ir_reader], [], []

            with mock.patch.multiple(managed_mapper, STATE_DIR=directory, APP_DIR=directory, STOP_REQUESTED=False), \
                    mock.patch.multiple(
                        managed_mapper.upstream,
                        WEBOS_MAJOR_VERSION=10,
                        EXCLUSIVE_MODE=True,
                        IR_INPUT_ENABLED=True,
                        IR_EXCLUSIVE_MODE=True,
                    ), \
                    mock.patch.object(managed_mapper.upstream, "read_input_devices", return_value=device_paths), \
                    mock.patch.object(managed_mapper.fcntl, "ioctl"), \
                    mock.patch.object(managed_mapper.select, "select", side_effect=readable), \
                    mock.patch.object(managed_mapper.upstream, "fire_events") as fire_events:
                managed_mapper.input_loop({"back": action})
                status = json.loads(Path(directory, "status.json").read_text())
            self.assertEqual(status["irInputDevice"], ir_path)
            self.assertEqual(fire_events.call_args_list, [mock.call([action])])

    def test_actions_for_app_survives_luna_error(self):
        actions = [
            {"function": "screen_off", "inputs": {}},
            {"function": "disabled", "inputs": {}, "appId": "cdp-30"},
            {"function": "toggle_piccap", "inputs": {}, "appId": "!"},
        ]
        with mock.patch.object(managed_mapper.upstream, "luna_send", side_effect=OSError("boom")):
            filtered = managed_mapper.actions_for_app(actions)
        self.assertEqual(filtered, [{"function": "screen_off", "inputs": {}}])

    def test_optional_ir_eof_detaches_without_stopping_primary(self):
        with tempfile.TemporaryDirectory() as directory:
            bt_path = os.path.join(directory, "bt")
            ir_path = os.path.join(directory, "ir")
            output_path = os.path.join(directory, "output")
            bt_events = self.back_events()
            Path(bt_path).write_bytes(bt_events)
            Path(ir_path).write_bytes(b"")
            Path(output_path).touch()
            device_paths = [
                ("LGE M-RCU - Builtin [0]", bt_path),
                ("LGE RCU", ir_path),
                ("LGE M-RCU - Builtin [1]", output_path),
            ]
            action = {"function": "screen_off", "inputs": {}}
            step = {"count": 0}

            def readable(readers, writers, errors, timeout):
                del writers, errors, timeout
                step["count"] += 1
                if step["count"] == 1:
                    ir_reader = next(reader for reader in readers if reader.name == ir_path)
                    return [ir_reader], [], []
                bt_reader = next(reader for reader in readers if reader.name == bt_path)
                if bt_reader.tell() == len(bt_events):
                    managed_mapper.STOP_REQUESTED = True
                    return [], [], []
                return [bt_reader], [], []

            with mock.patch.multiple(managed_mapper, STATE_DIR=directory, APP_DIR=directory, STOP_REQUESTED=False), \
                    mock.patch.multiple(
                        managed_mapper.upstream,
                        WEBOS_MAJOR_VERSION=10,
                        EXCLUSIVE_MODE=True,
                        IR_INPUT_ENABLED=True,
                        IR_EXCLUSIVE_MODE=True,
                    ), \
                    mock.patch.object(managed_mapper.upstream, "read_input_devices", return_value=device_paths), \
                    mock.patch.object(managed_mapper.fcntl, "ioctl"), \
                    mock.patch.object(managed_mapper.select, "select", side_effect=readable), \
                    mock.patch.object(managed_mapper.upstream, "fire_events") as fire_events:
                managed_mapper.input_loop({"back": action})
                status = json.loads(Path(directory, "status.json").read_text())
            self.assertIsNone(status["irInputDevice"])
            self.assertEqual(fire_events.call_args_list, [mock.call([action])])

    def test_primary_eof_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            bt_path = os.path.join(directory, "bt")
            output_path = os.path.join(directory, "output")
            Path(bt_path).write_bytes(b"")
            Path(output_path).touch()
            device_paths = [
                ("LGE M-RCU - Builtin [0]", bt_path),
                ("LGE M-RCU - Builtin [1]", output_path),
            ]
            with mock.patch.multiple(managed_mapper, STATE_DIR=directory, APP_DIR=directory, STOP_REQUESTED=False), \
                    mock.patch.multiple(managed_mapper.upstream, WEBOS_MAJOR_VERSION=10, EXCLUSIVE_MODE=True, IR_INPUT_ENABLED=False), \
                    mock.patch.object(managed_mapper.upstream, "read_input_devices", return_value=device_paths), \
                    mock.patch.object(managed_mapper.fcntl, "ioctl"), \
                    mock.patch.object(managed_mapper.select, "select", side_effect=lambda r, w, e, t: (r, [], [])):
                managed_mapper.input_loop({})
                status = json.loads(Path(directory, "status.json").read_text())
            self.assertFalse(status["active"])


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.request_path = os.path.join(self.temp_dir.name, "request.json")
        self.result_path = os.path.join(self.temp_dir.name, "result.json")
        self.discovery = DiscoveryController(self.request_path, self.result_path, settle_seconds=0.2)

    def tearDown(self):
        self.temp_dir.cleanup()

    def request(self, timeout=12):
        with open(self.request_path, "w") as request_file:
            json.dump({"id": "request-1", "timeout": timeout}, request_file)

    def test_waits_for_discover_key_release_then_suppresses_next_press(self):
        self.request()
        self.assertEqual(self.discovery.poll({28}, now=10), "waiting_for_release")
        self.assertFalse(self.discovery.handle_key(28, 0, "ok", {28}, now=10.1))
        self.assertEqual(self.discovery.poll(set(), now=10.1), "settling")
        self.assertEqual(self.discovery.poll(set(), now=10.31), "armed")

        self.assertTrue(self.discovery.handle_key(1037, 1, "netflix", set(), now=10.4))
        self.assertTrue(self.discovery.handle_key(1037, 0, "netflix", {1037}, now=10.5))

        with open(self.result_path) as result_file:
            result = json.load(result_file)
        self.assertEqual(result["button"], "netflix")
        self.assertEqual(result["code"], 1037)
        self.assertEqual(self.discovery.phase, "complete")

    def test_times_out_without_capturing_a_button(self):
        self.request(timeout=3)
        self.discovery.poll(set(), now=20)
        self.discovery.poll(set(), now=20.3)
        self.assertEqual(self.discovery.poll(set(), now=23.1), "timed_out")
        with open(self.result_path) as result_file:
            self.assertEqual(json.load(result_file)["error"], "timeout")

    def test_back_cancels_without_becoming_a_discovered_button(self):
        self.request()
        self.discovery.poll(set(), now=30)
        self.discovery.poll(set(), now=30.3)
        self.assertTrue(self.discovery.handle_key(412, 1, None, set(), now=30.4))
        with open(self.result_path) as result_file:
            result = json.load(result_file)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "cancelled")
        self.assertTrue(self.discovery.handle_key(412, 0, None, {412}, now=30.5))
        self.assertFalse(self.discovery.suppressed_until_release)


class PassthroughWriteTests(unittest.TestCase):
    CLOSED_FD = 9999

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.device_path = os.path.join(self.temp_dir.name, "passthrough")
        open(self.device_path, "wb").close()
        self.open_fds = []

    def tearDown(self):
        for fd in self.open_fds:
            with contextlib.suppress(OSError):
                os.close(fd)
        self.temp_dir.cleanup()

    def track(self, fd):
        if fd is not None:
            self.open_fds.append(fd)
        return fd

    def written(self):
        with open(self.device_path, "rb") as device:
            return device.read()

    def test_reuses_the_descriptor_while_the_device_is_healthy(self):
        fd = self.track(os.open(self.device_path, os.O_WRONLY))
        self.assertEqual(write_passthrough(fd, self.device_path, b"ab"), fd)
        self.assertEqual(write_passthrough(fd, self.device_path, b"cd"), fd)
        self.assertEqual(self.written(), b"abcd")

    def test_reopens_and_still_delivers_the_event_after_the_node_resets(self):
        fd = self.track(write_passthrough(self.CLOSED_FD, self.device_path, b"xy"))
        self.assertIsNotNone(fd)
        self.assertNotEqual(fd, self.CLOSED_FD)
        self.assertEqual(self.written(), b"xy")

    def test_reports_no_descriptor_when_the_device_is_gone(self):
        os.remove(self.device_path)
        self.assertIsNone(write_passthrough(self.CLOSED_FD, self.device_path, b"xy"))

    def test_recovers_on_a_later_event_once_the_device_returns(self):
        os.remove(self.device_path)
        output_device = write_passthrough(self.CLOSED_FD, self.device_path, b"lost")
        self.assertIsNone(output_device)
        open(self.device_path, "wb").close()
        self.track(write_passthrough(output_device, self.device_path, b"back"))
        self.assertEqual(self.written(), b"back")


class InputDeviceReadTests(unittest.TestCase):
    """An evdev node delivers a key and its SYN_REPORT in a single write."""

    EVENT_FORMAT = "llHHi"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp_dir.name, "event-node")
        os.mkfifo(self.path)
        self.writer = os.open(self.path, os.O_RDWR)
        self.reader = None

    def tearDown(self):
        if self.reader is not None:
            self.reader.close()
        os.close(self.writer)
        self.temp_dir.cleanup()

    def event(self, event_type, code, value):
        return struct.pack(self.EVENT_FORMAT, 0, 0, event_type, code, value)

    def drain(self, reader, limit=8):
        size = struct.calcsize(self.EVENT_FORMAT)
        collected = []
        for _ in range(limit):
            readable, _, _ = select.select([reader], [], [], 0.2)
            if not readable:
                break
            chunk = reader.read(size)
            if len(chunk) != size:
                break
            collected.append(struct.unpack(self.EVENT_FORMAT, chunk)[2:])
        return collected

    def test_select_loop_sees_the_sync_that_follows_a_keypress(self):
        os.write(self.writer, self.event(1, 115, 1) + self.event(0, 0, 0))
        self.reader = open_input_device(self.path)
        self.assertEqual(self.drain(self.reader), [(1, 115, 1), (0, 0, 0)])

    def test_select_loop_sees_a_full_press_and_release_written_together(self):
        batch = (
            self.event(1, 115, 1)
            + self.event(0, 0, 0)
            + self.event(1, 115, 0)
            + self.event(0, 0, 0)
        )
        os.write(self.writer, batch)
        self.reader = open_input_device(self.path)
        self.assertEqual(
            self.drain(self.reader),
            [(1, 115, 1), (0, 0, 0), (1, 115, 0), (0, 0, 0)],
        )


class HookTests(unittest.TestCase):
    def setUp(self):
        import mapperctl

        self.mapperctl = mapperctl
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.hook = self.root / "z-magic-mapper"
        self.prev_70 = self.root / "70-magic-mapper"
        self.prev_50 = self.root / "50-magic-mapper"
        self.legacy = self.root / "start_magic_mapper"
        self.log_path = self.root / "state" / "magic-mapper.log"
        for name, value in (
            ("HOOK_PATH", str(self.hook)),
            ("PREVIOUS_HOOKS", (str(self.prev_70), str(self.prev_50))),
            ("LEGACY_HOOK", str(self.legacy)),
            ("STATE_DIR", str(self.root / "state")),
            ("LOG_PATH", str(self.log_path)),
        ):
            patcher = mock.patch.object(mapperctl, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_hook_runs_after_luma_home_and_entware(self):
        # Homebrew runs hooks sequentially in name order; sort after both 64-luma-home and S99entware.
        import mapperctl

        hook_name = os.path.basename(mapperctl.HOOK_PATH)
        self.assertGreater(hook_name, "64-luma-home")
        self.assertGreater(hook_name, "S99entware")
        self.assertIn("70-magic-mapper", [os.path.basename(path) for path in mapperctl.PREVIOUS_HOOKS])
        self.assertIn("50-magic-mapper", [os.path.basename(path) for path in mapperctl.PREVIOUS_HOOKS])

    def test_start_moves_previous_hooks(self):
        self.prev_70.write_text("old 70 hook\n")
        self.prev_50.write_text("old 50 hook\n")
        with mock.patch.object(self.mapperctl, "start_mapper", return_value=(42, True)), \
                mock.patch.object(self.mapperctl, "status", return_value={}), \
                mock.patch.object(sys, "argv", ["mapperctl.py", "start"]), \
                contextlib.redirect_stdout(open(os.devnull, "w")):
            self.assertEqual(self.mapperctl.main(), 0)
        self.assertFalse(self.prev_70.exists())
        self.assertFalse(self.prev_50.exists())
        self.assertIn('mapperctl.sh" start', self.hook.read_text())
        self.assertTrue(os.access(self.hook, os.X_OK))

    def test_start_does_not_create_a_missing_hook(self):
        with mock.patch.object(self.mapperctl, "start_mapper", return_value=(42, True)), \
                mock.patch.object(self.mapperctl, "status", return_value={}), \
                mock.patch.object(sys, "argv", ["mapperctl.py", "start"]), \
                contextlib.redirect_stdout(open(os.devnull, "w")):
            self.assertEqual(self.mapperctl.main(), 0)
        self.assertFalse(self.hook.exists())

    def test_install_replaces_previous_hook_names(self):
        self.prev_70.write_text("old 70 hook\n")
        self.prev_50.write_text("old 50 hook\n")
        self.legacy.write_text("legacy hook\n")
        with mock.patch.object(self.mapperctl, "ensure_state"), \
                mock.patch.object(self.mapperctl, "atomic_write_json"), \
                mock.patch.object(self.mapperctl, "stop_legacy_mapper"), \
                mock.patch.object(self.mapperctl, "start_mapper", return_value=(42, True)):
            self.mapperctl.install()
        self.assertTrue(self.hook.exists())
        self.assertFalse(self.prev_70.exists())
        self.assertFalse(self.prev_50.exists())
        self.assertFalse(self.legacy.exists())

    def test_remove_deletes_every_hook_name(self):
        for path in (self.hook, self.prev_70, self.prev_50, self.legacy):
            path.write_text("hook\n")
        with mock.patch.object(self.mapperctl, "stop_mapper"):
            self.mapperctl.remove()
        for path in (self.hook, self.prev_70, self.prev_50, self.legacy):
            self.assertFalse(path.exists())

    def test_status_counts_an_unmigrated_hook_as_installed(self):
        self.prev_70.write_text("old hook\n")
        with mock.patch.object(self.mapperctl, "read_pid", return_value=None), \
                mock.patch.object(self.mapperctl, "process_matches", return_value=False):
            self.assertTrue(self.mapperctl.status()["installed"])

    def test_python_executable_prefers_running_python3_and_skips_python2(self):
        self.assertEqual(self.mapperctl.python_executable(), sys.executable)
        with mock.patch.object(sys, "executable", ""), \
                mock.patch.object(os.path, "isfile", side_effect=lambda path: path == "/opt/bin/python3"), \
                mock.patch.object(os, "access", side_effect=lambda path, mode: path == "/opt/bin/python3"):
            self.assertEqual(self.mapperctl.python_executable(), "/opt/bin/python3")

    def test_luna_send_args_matches_webos_major_version(self):
        with mock.patch.object(self.mapperctl, "get_webos_version", return_value=10):
            self.assertEqual(self.mapperctl.luna_send_args(), ["/usr/bin/luna-send", "-t", "1"])
        with mock.patch.object(self.mapperctl, "get_webos_version", return_value=6):
            self.assertEqual(self.mapperctl.luna_send_args(), ["/usr/bin/luna-send", "-n", "1"])

    def test_trim_log_caps_size_on_line_boundary(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("".join(f"line {i:04d}\n" for i in range(400)))
        self.mapperctl.trim_log(max_bytes=512)
        trimmed = self.log_path.read_text()
        self.assertLessEqual(len(trimmed.encode("utf-8")), 512)
        self.assertTrue(trimmed.startswith("line "))
        self.assertTrue(trimmed.endswith("line 0399\n"))

    def test_last_log_line_ignores_prior_session_lines(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("Magic Mapper is running\n")
        offset = os.path.getsize(self.log_path)
        self.assertEqual(self.mapperctl.last_log_line(since_offset=offset), "")
        with open(self.log_path, "a") as log_file:
            log_file.write("RuntimeError: no input device\n")
        self.assertEqual(
            self.mapperctl.last_log_line(since_offset=offset),
            "RuntimeError: no input device",
        )


class LauncherScriptTests(unittest.TestCase):
    LAUNCHER = os.path.join(ROOT_DIR, "runtime", "mapperctl.sh")

    def make_executable(self, path, content):
        path.write_text(content)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_falls_back_to_second_python3_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_python = self.make_executable(root / "old-python", "#!/bin/sh\nexit 1\n")
            good_python = self.make_executable(
                root / "opt-python3",
                f"#!/bin/sh\nexec \"{sys.executable}\" \"$@\"\n",
            )
            env = dict(
                os.environ,
                MAGIC_MAPPER_PYTHON_PATHS=f"{old_python}:{good_python}",
                MAGIC_MAPPER_ENTWARE_ROOT=str(root / "missing-helper"),
                MAGIC_MAPPER_SKIP_PATH="1",
            )
            proc = subprocess.run(
                ["/bin/sh", self.LAUNCHER, "discovery-result", "probe"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["requestId"], "probe")

    def test_falls_back_to_entware_root_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = self.make_executable(
                root / "webos-entware-root",
                f"#!/bin/sh\nif [ \"$1\" = \"run\" ] && [ \"$2\" = \"/opt/bin/python3\" ]; then shift 2; exec \"{sys.executable}\" \"$@\"; fi\nexit 1\n",
            )
            env = dict(
                os.environ,
                MAGIC_MAPPER_PYTHON_PATHS=f"{root / 'missing1'}:{root / 'missing2'}",
                MAGIC_MAPPER_ENTWARE_ROOT=str(helper),
                MAGIC_MAPPER_SKIP_PATH="1",
            )
            proc = subprocess.run(
                ["/bin/sh", self.LAUNCHER, "discovery-result", "entware-probe"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["requestId"], "entware-probe")

    def test_reports_missing_python3_as_structured_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(
                os.environ,
                MAGIC_MAPPER_PYTHON_PATHS=f"{root / 'missing1'}:{root / 'missing2'}",
                MAGIC_MAPPER_ENTWARE_ROOT=str(root / "missing-helper"),
                MAGIC_MAPPER_SKIP_PATH="1",
            )
            proc = subprocess.run(
                ["/bin/sh", self.LAUNCHER, "status"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout.strip())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "missing_python3")
        self.assertIn("/opt/bin/python3", payload["error"])


if __name__ == "__main__":
    unittest.main()

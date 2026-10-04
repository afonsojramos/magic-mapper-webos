import contextlib
import hashlib
import json
import os
import select
import struct
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
            set(managed_mapper.upstream.CONFIG_FUNCTIONS) - {"toggle_bluetooth"},
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
        settings = {"block_mouse": True}
        self.assertIs(validate_settings(settings), settings)
        with self.assertRaisesRegex(ValueError, "true or false"):
            validate_settings({"block_mouse": "yes"})
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


if __name__ == "__main__":
    unittest.main()

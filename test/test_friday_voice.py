"""
Offline unit tests for friday_voice.py.

No microphone, no ESP32, no internet, no Whisper model download required:
everything external is mocked. Run with:

    python -m unittest test.test_friday_voice -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import friday_voice as fv  # noqa: E402

import requests  # noqa: E402


class TestLightParsing(unittest.TestCase):
    def test_light_on_variants(self):
        for phrase in ["turn on light", "light on", "Turn on the light.",
                       "please turn on the lights"]:
            self.assertEqual(fv.parse_command(phrase), "light_on", phrase)

    def test_light_off_variants(self):
        for phrase in ["turn off light", "light off", "Turn off the light."]:
            self.assertEqual(fv.parse_command(phrase), "light_off", phrase)

    def test_common_mishearings_still_parse(self):
        # Whisper often mangles short commands; these stay unambiguous
        # because an on/off word is still required.
        self.assertEqual(fv.parse_command("like on"), "light_on")
        self.assertEqual(fv.parse_command("lite off"), "light_off")
        self.assertEqual(fv.parse_command("fun off"), "fan_off")
        self.assertEqual(fv.parse_command("van on"), "fan_on")


class TestFanParsing(unittest.TestCase):
    def test_fan_commands(self):
        self.assertEqual(fv.parse_command("turn on fan"), "fan_on")
        self.assertEqual(fv.parse_command("fan on"), "fan_on")
        self.assertEqual(fv.parse_command("turn off fan"), "fan_off")
        self.assertEqual(fv.parse_command("fan off"), "fan_off")


class TestApplianceParsing(unittest.TestCase):
    def test_appliance_commands(self):
        self.assertEqual(fv.parse_command("turn on appliance"), "appliance_on")
        self.assertEqual(fv.parse_command("appliance on"), "appliance_on")
        self.assertEqual(fv.parse_command("turn off appliance"), "appliance_off")
        self.assertEqual(fv.parse_command("appliance off"), "appliance_off")


class TestRelay4Parsing(unittest.TestCase):
    def test_relay4_commands(self):
        self.assertEqual(fv.parse_command("turn on appliance four"), "relay4_on")
        self.assertEqual(fv.parse_command("turn off appliance four"), "relay4_off")
        self.assertEqual(fv.parse_command("turn on appliance 4"), "relay4_on")
        self.assertEqual(fv.parse_command("turn off appliance 4"), "relay4_off")
        self.assertEqual(fv.parse_command("relay 4 on"), "relay4_on")
        self.assertEqual(fv.parse_command("relay 4 off"), "relay4_off")
        self.assertEqual(fv.parse_command("relay four on"), "relay4_on")


class TestAllOffParsing(unittest.TestCase):
    def test_all_off_commands(self):
        for phrase in ["turn everything off", "turn all off",
                       "switch everything off", "all off"]:
            self.assertEqual(fv.parse_command(phrase), "all_off", phrase)


class TestHinglishParsing(unittest.TestCase):
    def test_hinglish_commands(self):
        cases = {
            "light chalu karo": "light_on",
            "light band karo": "light_off",
            "batti chalu karo": "light_on",
            "batti band karo": "light_off",
            "fan chalu karo": "fan_on",
            "fan band karo": "fan_off",
            "appliance chalu karo": "appliance_on",
            "appliance band karo": "appliance_off",
            "sab band karo": "all_off",
            "sab kuch band karo": "all_off",
        }
        for phrase, expected in cases.items():
            self.assertEqual(fv.parse_command(phrase), expected, phrase)


class TestInvalidCommands(unittest.TestCase):
    def test_rejects_garbage_and_ambiguity(self):
        self.assertIsNone(fv.parse_command(""))
        self.assertIsNone(fv.parse_command("   "))
        self.assertIsNone(fv.parse_command("hello there"))
        self.assertIsNone(fv.parse_command("turn on"))
        self.assertIsNone(fv.parse_command("light"))
        self.assertIsNone(fv.parse_command("open the pod bay doors"))
        self.assertIsNone(fv.parse_command("turn the light on and off"))
        self.assertIsNone(fv.parse_command("television on"))

    def test_none_input(self):
        self.assertIsNone(fv.parse_command(None))


class TestEndpointWhitelist(unittest.TestCase):
    def test_whitelist_is_exactly_the_fixed_set(self):
        self.assertEqual(
            set(fv.ENDPOINTS.values()),
            {"/light/on", "/light/off", "/fan/on", "/fan/off",
             "/appliance/on", "/appliance/off", "/relay4/on", "/relay4/off",
             "/all/off"},
        )

    def test_unknown_command_has_no_endpoint(self):
        self.assertIsNone(fv.endpoint_for("garage_open"))
        self.assertIsNone(fv.endpoint_for("../../etc/passwd"))

    def test_send_command_refuses_non_whitelisted(self):
        with mock.patch.object(fv.requests, "get") as fake_get:
            ok, error = fv.send_command("garage_open")
        self.assertFalse(ok)
        fake_get.assert_not_called()

    def test_send_command_builds_url_from_whitelist_only(self):
        response = mock.Mock(status_code=200)
        with mock.patch.object(fv.requests, "get", return_value=response) as fake_get:
            ok, error = fv.send_command("light_on")
        self.assertTrue(ok)
        self.assertIsNone(error)
        fake_get.assert_called_once_with(
            fv.ESP32_BASE_URL.rstrip("/") + "/light/on",
            timeout=fv.HTTP_TIMEOUT_SECONDS,
        )


class TestDuplicateSuppression(unittest.TestCase):
    def setUp(self):
        fv.reset_duplicate_guard()

    def test_same_command_within_window_is_duplicate(self):
        self.assertFalse(fv.is_duplicate("light_on", now=100.0))
        self.assertTrue(fv.is_duplicate("light_on", now=101.0))

    def test_same_command_after_window_is_allowed(self):
        self.assertFalse(fv.is_duplicate("light_on", now=100.0))
        self.assertFalse(fv.is_duplicate("light_on", now=102.5))

    def test_different_command_is_never_duplicate(self):
        self.assertFalse(fv.is_duplicate("light_on", now=100.0))
        self.assertFalse(fv.is_duplicate("light_off", now=100.5))


class TestHttpFailureHandling(unittest.TestCase):
    def test_connection_error_is_caught(self):
        with mock.patch.object(
            fv.requests, "get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            ok, error = fv.send_command("fan_on")
        self.assertFalse(ok)
        self.assertIn("ConnectionError", error)

    def test_timeout_is_caught(self):
        with mock.patch.object(
            fv.requests, "get",
            side_effect=requests.exceptions.Timeout("too slow"),
        ):
            ok, error = fv.send_command("all_off")
        self.assertFalse(ok)

    def test_non_200_is_failure(self):
        response = mock.Mock(status_code=404)
        with mock.patch.object(fv.requests, "get", return_value=response):
            ok, error = fv.send_command("light_off")
        self.assertFalse(ok)
        self.assertIn("404", error)

    def test_check_esp32_handles_unreachable(self):
        with mock.patch.object(
            fv.requests, "get",
            side_effect=requests.exceptions.ConnectionError("no route"),
        ):
            self.assertFalse(fv.check_esp32())

    def test_check_esp32_ok(self):
        response = mock.Mock(status_code=200)
        with mock.patch.object(fv.requests, "get", return_value=response):
            self.assertTrue(fv.check_esp32())


class _FakeAudio:
    """Stand-in for speech_recognition.AudioData (16 kHz, 16-bit mono).

    Defaults to a clearly audible constant tone (amplitude 3000 of 32768)
    so it passes the silence gate; pass raw=b"\\x00\\x00" * N for silence.
    """

    def __init__(self, raw=(3000).to_bytes(2, "little", signed=True) * 1600):
        self._raw = raw

    def get_raw_data(self, convert_rate=None, convert_width=None):
        return self._raw


class TestWhisperFailureHandling(unittest.TestCase):
    def test_model_load_failure_returns_none(self):
        with mock.patch.object(
            fv, "get_whisper_model",
            side_effect=RuntimeError("model missing"),
        ):
            self.assertIsNone(fv.transcribe_audio(_FakeAudio()))

    def test_transcribe_call_failure_returns_none(self):
        broken_model = mock.Mock()
        broken_model.transcribe.side_effect = RuntimeError("boom")
        with mock.patch.object(fv, "get_whisper_model", return_value=broken_model):
            self.assertIsNone(fv.transcribe_audio(_FakeAudio()))

    def test_successful_transcription_returns_stripped_text(self):
        model = mock.Mock()
        model.transcribe.return_value = {"text": "  turn on light  "}
        with mock.patch.object(fv, "get_whisper_model", return_value=model):
            self.assertEqual(fv.transcribe_audio(_FakeAudio()), "turn on light")

    def test_near_silent_audio_skips_whisper(self):
        model = mock.Mock()
        with mock.patch.object(fv, "get_whisper_model", return_value=model):
            result = fv.transcribe_audio(_FakeAudio(raw=b"\x00\x00" * 1600))
        self.assertEqual(result, "")
        model.transcribe.assert_not_called()

    def test_transcription_uses_command_vocabulary_prompt(self):
        model = mock.Mock()
        model.transcribe.return_value = {"text": "fan on"}
        with mock.patch.object(fv, "get_whisper_model", return_value=model):
            fv.transcribe_audio(_FakeAudio())
        kwargs = model.transcribe.call_args.kwargs
        self.assertEqual(kwargs.get("initial_prompt"), fv.WHISPER_PROMPT)
        self.assertEqual(kwargs.get("temperature"), 0.0)
        self.assertFalse(kwargs.get("condition_on_previous_text"))

    def test_model_is_cached_not_reloaded(self):
        fake_whisper = mock.Mock()
        fake_whisper.load_model.return_value = mock.Mock(name="model")
        fv._whisper_model = None
        with mock.patch.dict(sys.modules, {"whisper": fake_whisper}):
            first = fv.get_whisper_model()
            second = fv.get_whisper_model()
        self.assertIs(first, second)
        fake_whisper.load_model.assert_called_once_with(fv.WHISPER_MODEL_NAME)
        fv._whisper_model = None  # reset for other tests


class TestHandleText(unittest.TestCase):
    """End-to-end (mocked) flow: text -> parse -> dedupe -> HTTP -> speech."""

    def setUp(self):
        fv.reset_duplicate_guard()

    def test_known_command_sends_request_and_confirms(self):
        response = mock.Mock(status_code=200)
        with mock.patch.object(fv.requests, "get", return_value=response) as fake_get, \
             mock.patch.object(fv, "speak") as fake_speak:
            command = fv.handle_text("turn on light")
        self.assertEqual(command, "light_on")
        fake_get.assert_called_once()
        fake_speak.assert_any_call("Turning on the light.")
        fake_speak.assert_any_call("Light turned on.")

    def test_unknown_command_speaks_error_and_sends_nothing(self):
        with mock.patch.object(fv.requests, "get") as fake_get, \
             mock.patch.object(fv, "speak") as fake_speak:
            command = fv.handle_text("make me a sandwich")
        self.assertIsNone(command)
        fake_get.assert_not_called()
        fake_speak.assert_called_once_with(fv.UNKNOWN_COMMAND_REPLY)

    def test_duplicate_command_only_executes_once(self):
        response = mock.Mock(status_code=200)
        with mock.patch.object(fv.requests, "get", return_value=response) as fake_get, \
             mock.patch.object(fv, "speak"):
            fv.handle_text("turn on light")
            fv.handle_text("turn on light")  # immediately repeated
        self.assertEqual(fake_get.call_count, 1)

    def test_unreachable_esp32_speaks_cannot_reach(self):
        with mock.patch.object(
            fv.requests, "get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ), mock.patch.object(fv, "speak") as fake_speak:
            fv.handle_text("fan off")
        fake_speak.assert_any_call(fv.UNREACHABLE_REPLY)


if __name__ == "__main__":
    unittest.main()

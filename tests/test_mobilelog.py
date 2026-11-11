import unittest
from unittest import mock

from adbperfetto import mobilelog
from adbperfetto.mobilelog import (
    MobileLog, MobileLogError, extract_logger_bigtext, other_log_names,
    parse_other_logs)


class LoggerNotificationTests(unittest.TestCase):
    def test_extracts_logger_notification_not_another_app(self):
        text = (
            "NotificationRecord(first pkg=com.other.app)\n"
            "  android.bigText=String (Modem Log is on.)\n"
            "NotificationRecord(second pkg=com.debug.loggerui)\n"
            "  android.bigText=String (Network,GPSHost Log are on.)\n"
        )
        self.assertEqual(extract_logger_bigtext(text),
                         "Network,GPSHost Log are on.")
        self.assertIsNone(
            extract_logger_bigtext("NotificationRecord(pkg=com.other.app)"))

    def test_known_notification_texts(self):
        cases = {
            "Modem,Network,GPSHost Log are on.": 22,
            "Network,GPSHost Log are on.": 20,
            "GPSHost Log is on.": 16,
            "Warning : All logs are stopped!!!": 0,
            "Mobile,Modem Log are on.": 2,
            "Mobile,Unknown Logs are on": 0,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_other_logs(text), expected)
        self.assertIsNone(parse_other_logs("garbage"))
        self.assertIsNone(parse_other_logs(None))

    def test_other_log_names(self):
        self.assertEqual(other_log_names(22), "Modem, Network, GPSHost")
        self.assertEqual(other_log_names(4, "/"), "Network")
        self.assertEqual(other_log_names(0), "")


class LifecycleTests(unittest.TestCase):
    def _mobile(self):
        return MobileLog(mock.Mock(), "SER", sleep=lambda s: None)

    def test_pause_records_mask_and_broadcasts(self):
        mobile = self._mobile()
        mobile.other_logs_state = mock.Mock(return_value=22)
        mobile.broadcast = mock.Mock()
        mobile.wait_other_logs = mock.Mock(return_value=True)
        mobile.pause_other_logs()
        mobile.broadcast.assert_called_once_with("stop", 22)
        self.assertEqual(mobile.paused_mask, 22)
        self.assertEqual(mobile.original_mask, 22)

    def test_unknown_or_stopped_state_does_not_broadcast(self):
        for state in (None, 0):
            with self.subTest(state=state):
                mobile = self._mobile()
                mobile.other_logs_state = mock.Mock(return_value=state)
                mobile.broadcast = mock.Mock()
                mobile.pause_other_logs()
                mobile.broadcast.assert_not_called()
                self.assertEqual(mobile.paused_mask, 0)

    def test_stop_restores_paused_logs_even_if_stop_fails(self):
        mobile = self._mobile()
        mobile.started = True
        mobile.paused_mask = 20
        mobile.wait_other_logs = mock.Mock(return_value=True)
        calls = []

        def broadcast(name, target=1):
            calls.append((name, target))
            if name == "stop":
                raise MobileLogError("stop failed")

        mobile.broadcast = mock.Mock(side_effect=broadcast)
        with self.assertRaises(MobileLogError):
            mobile.stop()
        self.assertIn(("start", 20), calls)
        self.assertEqual(mobile.paused_mask, 0)
        self.assertTrue(mobile.logs_restored)

    def test_broadcast_command_shape(self):
        adb = mock.Mock()
        adb.shell.return_value = mock.Mock(returncode=0, stdout="")
        mobile = MobileLog(adb, "SER123")
        mobile.broadcast("clear_all_logs")
        command = adb.shell.call_args[0][1]
        self.assertIn("com.debug.loggerui.ADB_CMD", command)
        self.assertIn("cmd_name clear_all_logs", command)
        self.assertIn("--ei cmd_target 1", command)
        self.assertEqual(adb.shell.call_args[0][0], "SER123")

    def test_state_dict_roundtrip(self):
        mobile = self._mobile()
        mobile.log_path = "/data/debuglogger/mobilelog/APLog_1"
        mobile.started = True
        mobile.paused_mask = 6
        mobile.original_mask = 6
        restored = MobileLog.from_state(
            mock.Mock(), "SER", mobile.state_dict())
        self.assertEqual(restored.log_path, mobile.log_path)
        self.assertTrue(restored.started)
        self.assertEqual(restored.paused_mask, 6)
        self.assertEqual(restored.original_mask, 6)


if __name__ == "__main__":
    unittest.main()

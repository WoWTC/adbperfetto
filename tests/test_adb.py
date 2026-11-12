import unittest

from adbperfetto.adb import Result, is_wifi_serial, parse_devices_output
from adbperfetto.device import probe
from tests.fake_adb import FakeAdb


class IsWifiSerialTests(unittest.TestCase):
    def test_ip_port_serials(self):
        self.assertTrue(is_wifi_serial("192.168.1.23:5555"))
        self.assertTrue(is_wifi_serial("10.0.0.2:37001"))
        self.assertTrue(is_wifi_serial("[fe80::1]:5555"))

    def test_mdns_serials(self):
        self.assertTrue(is_wifi_serial(
            "adb-Pixel8-abcdef._adb-tls-connect._tcp"))
        self.assertTrue(is_wifi_serial("Pixel8._adb._tcp"))

    def test_usb_and_emulator_serials(self):
        self.assertFalse(is_wifi_serial("emulator-5554"))
        self.assertFalse(is_wifi_serial("0123456789ABCDEF"))
        self.assertFalse(is_wifi_serial("KN8DEMO0001"))


class ParseDevicesTests(unittest.TestCase):
    def test_parses_long_output_and_model_hints(self):
        text = (
            "* daemon not running; starting now at tcp:5037\n"
            "* daemon started successfully\n"
            "List of devices attached\n"
            "18247156AB000012       device product:X1202-OP "
            "model:Infinix_X1202 device:Infinix-X1202 transport_id:7\n"
            "R58MDEMO               unauthorized usb:1-2 model:R58M\n"
            "emulator-5554          offline\n\n"
        )
        self.assertEqual(parse_devices_output(text), [
            ("18247156AB000012", "device", "Infinix X1202"),
            ("R58MDEMO", "unauthorized", "R58M"),
            ("emulator-5554", "offline", ""),
        ])


class DeviceProbeNameTests(unittest.TestCase):
    def test_extracts_tran_or_global_device_name(self):
        adb = FakeAdb()

        @adb.on(r"echo MODEL=")
        def _probe(_ctx):
            return Result(
                0,
                "MODEL=Infinix X1202\n"
                "MANUFACTURER=INFINIX\n"
                "MARKET_RO=\n"
                "MARKET_TRAN=XPAD Edge 2\n"
                "MARKET_OPLUS=\n"
                "DEV_NAME=XPAD Edge 2\n"
                "SDK=37\n"
                "RELEASE=17\n",
                "",
            )

        info = probe(adb, "18247156AB000012")
        self.assertEqual(info.model, "Infinix X1202")
        self.assertEqual(info.device_name, "XPAD Edge 2")

    def test_ignores_null_placeholder_device_name(self):
        adb = FakeAdb()

        @adb.on(r"echo MODEL=")
        def _probe(_ctx):
            return Result(
                0,
                "MODEL=Pixel 7\n"
                "MARKET_RO=\n"
                "MARKET_TRAN=\n"
                "DEV_NAME=null\n"
                "SDK=34\n",
                "",
            )

        info = probe(adb, "PIXEL7")
        self.assertEqual(info.model, "Pixel 7")
        self.assertEqual(info.device_name, "")


if __name__ == "__main__":
    unittest.main()

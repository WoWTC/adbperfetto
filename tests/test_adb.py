import unittest

from adbperfetto.adb import is_wifi_serial


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


if __name__ == "__main__":
    unittest.main()

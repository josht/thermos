"""An example of how to setup and start an Accessory.
This is:
1. Create the Accessory object you want.
2. Add it to an AccessoryDriver, which will advertise it on the local network,
    setup a server to answer client queries, etc.
"""
import logging
import signal

from prometheus_client import Gauge, start_http_server

from pyhap.accessory import Bridge
from pyhap.accessory_driver import AccessoryDriver
from RPi import GPIO

from devices import Thermostat

logging.basicConfig(level=logging.INFO, format="[%(module)s] %(asctime)ss %(message)s", datefmt='%Y-%m-%d %H:%M:%S')


def get_bridge(driver):
    """Call this method to get a Bridge instead of a standalone accessory."""
    bridge = Bridge(driver, 'ThermostatBridge')

    tstat1 = Thermostat(driver, 'Zone1')
    tstat2 = Thermostat(driver, 'Zone2')
    tstat3 = Thermostat(driver, 'Zone3')
    tstat4 = Thermostat(driver, 'Zone4')
    tstat5 = Thermostat(driver, 'Zone5')
    tstat6 = Thermostat(driver, 'Zone6')
    tstat7 = Thermostat(driver, 'Zone7')


    bridge.add_accessory(tstat1)
    bridge.add_accessory(tstat2)
    bridge.add_accessory(tstat3)
    bridge.add_accessory(tstat4)
    bridge.add_accessory(tstat5)
    bridge.add_accessory(tstat6)
    bridge.add_accessory(tstat7)

    return bridge


# Start the accessory on port 51826 & save the accessory.state to our custom path
driver = AccessoryDriver(port=51826, persist_file='./config/accessory.state')

# Change `get_accessory` to `get_bridge` if you want to run a Bridge.
driver.add_accessory(accessory=get_bridge(driver))

# use a gpio pin (22) to power the temp sensors
# this allows for power cycling the sensors when errors occur
vcc_pin = 0
GPIO.setup(vcc_pin, GPIO.OUT)
GPIO.output(vcc_pin, GPIO.HIGH)

# We want SIGTERM (terminate) to be handled by the driver itself,
# so that it can gracefully stop the accessory, server and advertising.
signal.signal(signal.SIGTERM, driver.signal_handler)

# Expose metrics
start_http_server(8080)

# Start it!
driver.start()

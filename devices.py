import time

import requests
import json
import logging

import redis
from RPi import GPIO
from pyhap.accessory import Accessory
from pyhap.const import CATEGORY_SENSOR, CATEGORY_THERMOSTAT
from w1thermsensor import W1ThermSensor, NoSensorFoundError, Unit
from prometheus_client import Gauge, Counter


# initialize prometheus metrics gauges
current_temp_gauge = Gauge(f"current_temperature", "Temperature in F", labelnames=["room", "heat_status"])
target_temp_gauge = Gauge(f"target_temperature", "Temperature in F", labelnames=["room", "heat_status"])
heat_status_gauge = Gauge(f"heat_status", "Heat On/Off Status", labelnames=["room"])
response_time_gauge = Gauge(f"response_time", "Temp Sensor Response Time", labelnames=["room"])
reset_error_counter = Counter(f"reset_error_count", "Sensor Reset Errors", labelnames=["room"])


class Thermostat(Accessory):
    category = CATEGORY_THERMOSTAT  # This is for the icon in the iOS Home app.

    @classmethod
    def _gpio_setup(_cls, relay_pin, temp_pin):
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BCM)

        # setup an input relay pin so I can check the status
        GPIO.setup(relay_pin, GPIO.IN)

        # setup an output relay pin so I can set the status
        GPIO.setup(relay_pin, GPIO.OUT)

        # old: set internal pullup resistor on temp sensor GPIO (~50k ohms) - value: GPIO.PUD_UP
        # new: disable internal pull up
        GPIO.setup(temp_pin, GPIO.IN, pull_up_down=GPIO.PUD_OFF)

    def __init__(self, *args, **kwargs):
        """Here, we just store a reference to the current temperature characteristic and
        add a method that will be executed every time its value changes.
        """
        # If overriding this method, be sure to call the super's implementation first.
        super().__init__(*args, **kwargs)

        # Add the services that this Accessory will support with add_preload_service here
        temp_service = self.add_preload_service('Thermostat')
        self.current_temp = temp_service.get_characteristic('CurrentTemperature')
        self.target_temp = temp_service.get_characteristic('TargetTemperature')
        self.target_state = temp_service.get_characteristic('TargetHeatingCoolingState')
        # self.current_state = temp_service.get_characteristic('CurrentHeatingCoolingState')

        # Default unit to Fahrenheit (change to 0 for Celcius)
        temp_service.configure_char('TemperatureDisplayUnits', value=1)

        # Having a callback is optional, but you can use it to add functionality.
        self.target_temp.setter_callback = self.target_temp_changed
        self.current_temp.setter_callback = self.current_temp_changed
        self.target_state.setter_callback = self.target_state_changed
        # self.current_state.setter_callback = self.current_state_changed

        # initialize redis connection per device
        self.r = redis.Redis(
            host='localhost',
            port=6379,
            password='',
            decode_responses=True)

        if not self.r.exists(self.display_name):
            self.r.set(self.display_name, '{}')

        state = json.loads(self.r.get(self.display_name))

        with open('config/config.json') as f:
            data = json.load(f)
            state['relay_pin'] = data[self.display_name]['relay_pin']
            state['temp_pin'] = data[self.display_name]['temp_pin']
            state['temp_id'] = data[self.display_name]['temp_id']
            # load extra_sensor url if one is defined
            if 'extra_sensor' in data[self.display_name]:
                state['extra_sensor'] = data[self.display_name]['extra_sensor']
                logging.info(f"{self.display_name} uses extra sensor {state['extra_sensor']}")

        # initialize gpio
        self.relay_pin = state['relay_pin']
        self.temp_pin = state['temp_pin']
        self._gpio_setup(self.relay_pin, self.temp_pin)

        # sane defaults for target temp if it doesn't already exist
        state['target_temp'] = state.get('target_temp', 70)
        self.target_temp.set_value(state['target_temp'])

        # sane defaults for target state if it doesn't already exist
        state['target_state'] = state.get('target_state', 0)
        self.target_state.set_value(state['target_state'])

        self.r.set(self.display_name, json.dumps(state))

        self.prev_status = ''

    def target_state_changed(self, value):
        """This will be called every time the value of the CurrentTemperature
        is changed. Use setter_callbacks to react to user actions, e.g. setting the
        lights On could fire some GPIO code to turn on a LED (see pyhap/accessories/LightBulb.py).
        """

        # get existing target_state
        json_state = json.loads(self.r.get(self.display_name))

        # set new target_state
        json_state['target_state'] = value
        self.r.set(self.display_name, json.dumps(json_state))

        print('Target State changed to: ', value)

    def target_temp_changed(self, value):
        # self.temp_target.set_value(value)

        # get existing target_temp
        json_state = json.loads(self.r.get(self.display_name))

        # set new target_temp
        json_state['target_temp'] = value
        self.r.set(self.display_name, json.dumps(json_state))
        print('Temperature [TARGET] changed to: ', value)

    def current_temp_changed(self, value):
        """This will be called every time the value of the CurrentTemperature
        is changed. Use setter_callbacks to react to user actions, e.g. setting the
        lights On could fire some GPIO code to turn on a LED (see pyhap/accessories/LightBulb.py).
        """
        print('Temperature [CURRENT] changed to: ', value)

    # Run this if no sensor is found. Default temp to 72
    def runNoSensor(self):
        data = json.loads(self.r.get(self.display_name))
        response_time = None
        start = time.process_time()

        # Set fake temp
        temp = 22.2222

        self.current_temp.set_value(temp)

        response_time = time.process_time() - start

        # response time for temperature sensor
        response_time_gauge.labels(room=self.display_name).set(response_time)

        status = ''

        self.processTemp()
        
        # to fahrenheit
        d = u"\u00b0"
        cf = round(9.0/5.0 * self.current_temp.value + 32, 2)
        tf = round(9.0/5.0 * self.target_temp.value + 32, 2)
        logging.info(f'{self.display_name} (Current:{cf}{d}F Target:{tf}{d}F) {status}')

    
    def processTemp(self):
        HEAT_ON = GPIO.LOW
        HEAT_OFF = GPIO.HIGH
        # check that we want heat
        if self.target_state.value == 0:
            # if heat relay is already on, check if above threshold
            # if above, turn off... if still below keep on
            if GPIO.input(self.relay_pin):
                if self.current_temp.value - self.target_temp.value >= 0.5:
                    status = 'HEAT ON - TEMP IS ABOVE TOP THRESHOLD, TURNING OFF'
                    GPIO.output(self.relay_pin, HEAT_OFF)
                else:
                    status = 'HEAT ON - TEMP IS BELOW TOP THRESHOLD, KEEPING ON'
                    GPIO.output(self.relay_pin, HEAT_ON)
            # if heat relay is not already on, check if below threshold
            elif not GPIO.input(self.relay_pin):
                if self.current_temp.value - self.target_temp.value <= -0.5:
                    status = 'HEAT OFF - TEMP IS BELOW BOTTOM THRESHOLD, TURNING ON'
                    GPIO.output(self.relay_pin, HEAT_ON)
                else:
                    status = 'HEAT OFF - KEEPING OFF'
        else:
            # turn off heat
            status = 'HEAT OFF - NOT REQUESTED'
            GPIO.output(self.relay_pin, HEAT_OFF)

        if status == self.prev_status:
            status = ''
        else:
            self.prev_status = status

        # to fahrenheit
        d = u"\u00b0"
        cf = round(9.0/5.0 * self.current_temp.value + 32, 2)
        tf = round(9.0/5.0 * self.target_temp.value + 32, 2)

        # set metric values for prometheus
        current_temp_gauge.labels(room=self.display_name, heat_status=self.target_state.value).set(cf)
        target_temp_gauge.labels(room=self.display_name, heat_status=self.target_state.value).set(tf)
        heat_status_gauge.labels(room=self.display_name).set(GPIO.input(self.relay_pin))


    @Accessory.run_at_interval(3)  # Run this method every 3 seconds
    # The `run` method can be `async` as well
    async def run(self):
        """We override this method to implement what the accessory will do when it is
        started.

        We set the current temperature to a random number. The decorator runs this method
        every 3 seconds.
        """
        try:
            sensors = W1ThermSensor().get_available_sensors()
        except NoSensorFoundError:
            # attempt to solve "Task exception was never retrieved" and "w1thermsensor.errors.NoSensorFoundError"
            logging.error('NoSensorFoundError')
            return self.runNoSensor()

        for sensor in sensors:

            data = json.loads(self.r.get(self.display_name))
            response_time = None

            # get temperature
            if sensor.id == data['temp_id']:
                try:
                    start = time.process_time()
                    if 'extra_sensor' not in data:
                        # use thermostat temperature sensor
                        temp = sensor.get_temperature(Unit.DEGREES_C)
                    else:
                        try:
                            resp = requests.get(data['extra_sensor'], timeout=3)
                            # throw exception if non-200
                            resp.raise_for_status()
                            temp = resp.json()['temp_c']
                        except requests.exceptions.RequestException as error:
                            # if extra_sensor fails, default to thermostat temperature sensor
                            temp = sensor.get_temperature(Unit.DEGREES_C)
                            logging.error(f'{self.display_name} extra_sensor is unavailable using {sensor.id} - {error}')

                    # power cycle vcc for temp sensors if we get an error reading
                    vcc_pin = 22
                    # If greater than 100F or less than 32F
                    # ie. read error returns -172C
                    if temp > 37 or temp < 0:
                        logging.error(f'{self.display_name} reading out of range - power cycling')
                        GPIO.output(vcc_pin, GPIO.LOW)
                        time.sleep(1)
                        GPIO.output(vcc_pin, GPIO.HIGH)

                    self.current_temp.set_value(temp)

                    response_time = time.process_time() - start
                except IndexError as error:
                    response_time = time.process_time() - start
                    logging.error(f'{self.display_name} temperature sensor is unavailable - {error}')
                    return
                except Exception as exception:
                    response_time = time.process_time() - start
                    reset_error_counter.labels(room=self.display_name).inc()
                    logging.error(f'{self.display_name} - {exception}')
                    return

                # response time for temperature sensor
                response_time_gauge.labels(room=self.display_name).set(response_time)

                status = ''

                self.processTemp()

                # to fahrenheit
                d = u"\u00b0"
                cf = round(9.0/5.0 * self.current_temp.value + 32, 2)
                tf = round(9.0/5.0 * self.target_temp.value + 32, 2)
                logging.info(f'{self.display_name} (Current:{cf}{d}F Target:{tf}{d}F) {status}')

    # The `stop` method can be `async` as well
    def stop(self):
        """We override this method to clean up any resources or perform final actions, as
        this is called by the AccessoryDriver when the Accessory is being stopped.
        """
        print('Stopping accessory.')
import logging
import json
import urllib.parse
import urllib.request



logger = logging.getLogger('HUE')


class Bridge():
    """
    Communication with a Hue bridge.
    """

    @classmethod
    def register(cls, host, device_type='hue2mqtt'):
        """
        Sign up with the bridge and retrieve an API key.
        Press the button on the bridge, then call this function.
        """
        url  = 'http://{:}/api'.format(host)
        data = json.dumps({ 'devicetype': device_type }).encode('utf-8')
        req  = urllib.request.Request(url=url, method='POST', data=data)

        logger.warn('Press the button on the bridge now!')
        with urllib.request.urlopen(req) as conn:
            for response in json.loads(conn.read()):
                if 'success' in response:
                    logger.info('Registration with Hue bridge successful!')
                    return response['success']['username']
                elif 'error' in response:
                    logger.error('Registration failed with code {:}: {:}'.format(
                                response['error']['type'], response['error']['description']))
                    return None
        return None


    def __init__(self, host, auth):
        """
        Constructor.

        Connect and list all accessories.
        """
        self.host = host
        self.auth = auth

        self.devices = dict()
        for kind in [ 'sensors', 'lights' ]:
            # retrieve list of sensors/lights
            for index, data in self._execute(kind).items():
                # create accessory objects
                device = Accessory.from_json(self, kind, index, data)
                if device is not None:
                    # store list of accessories which can be managed by this library
                    self.devices[kind+device.index] = device

    def __iter__(self):
        """
        Iterator protocol, iterating over all connected accessories.
        """
        yield from self.devices.values()

    def _execute(self, path, method='GET', data=None):
        """
        Send a request/command to the bridge.

        Payload `data` should be a `dict` and will be converted to JSON.
        """
        url  = 'http://{:}/api/{:}/{:}'.format(self.host, self.auth, path)
        data = json.dumps(data).encode('utf-8') if (data is not None) else None
        logger.debug('{:} {:} << {:}'.format(method, url, data))

        req  = urllib.request.Request(url=url, method=method, data=data)
        with urllib.request.urlopen(req) as conn:
            logger.debug('Response: {:} {:}'.format(conn.status, conn.reason))
            return json.loads(conn.read())

    def by_uid(self, uid):
        """
        Find a device by its unique ID.
        """
        for device in self:
            if device.uid == uid:
                return device

        return None

    def update(self, kind):
        """
        Update the status of all lights and sensors.

        Returns a list of devices whose status has changed.
        """
        changed = []

        for index, data in self._execute(kind).items():
            if 'uniqueid' not in data:
                continue

            device = self.by_uid(data['uniqueid'])
            if (device is not None) and device.parse(data):
                changed.append(device)

        return changed


class Accessory(object):
    """
    Generic accessory class.

    Maintains a refernce to Hue* helper classes
    for reading/setting the device properties.
    """
    
    @classmethod
    def from_json(cls, bridge, kind, index, data):
        """
        Construct a new accessory object.

        Parameters:
        * `bridge` is a `Bridge` object.
        * `kind` must be `sensors` or `lights`.
        * `index` is the index in the bridge.
        * `data` is the JSON object returned by the bridge for this device.
        """

        # devices must have an ID
        if 'uniqueid' not in data:
            return None

        # devices must have basic Hue properties
        if not HueGeneric.is_applicable(data):
            return None
        
        # now test for additional features
        all_handlers = [ HueGeneric,
                         HueLight,
                         HueColorTemperature,
                         HueBatteryStatus,
                         HueDimmerSwitch,
                         HueLightSensor,
                         HuePresenceSensor,
                         HueTemperatureSensor ]

        # list all classes which understand (a part of) this device
        handlers = [ handler() for handler in all_handlers if handler.is_applicable(data) ]

        # build sensor object and parse status
        obj = cls(bridge, kind, index)
        obj.uid = data['uniqueid']
        obj.handlers = handlers
        obj.parse(data)
        return obj

    def __init__(self, bridge, kind, index):
        """
        Constructor.

        Do not call manually, use `from_json` instead.
        """
        self.bridge   = bridge
        self.kind     = kind
        self.index    = index
        self.uid      = None
        self.handlers = []
        self.data     = dict()

    def set(self, **kwargs):
        """
        Change device properties.

        Pass parameters to be changed as kwargs.
        """
        changes = dict()
        for handler in self.handlers:
            this_change = handler.set(**kwargs)
            for block, block_changes in this_change.items():
                changes.setdefault(block, dict())
                changes[block].update(block_changes)

        for block, block_changes in changes.items():
            block_changes = { k: v for k, v in block_changes.items() if v is not None }
            if len(block_changes) > 0:
                self.bridge._execute('{:}/{:}/{:}'.format(self.kind, self.index, block),
                                     'PUT', block_changes)

    def parse(self, data):
        """
        Update the device status from the JSON object returned by the Hue bridge.
        """
        def _update_changed(d1, d2):
            changed = False
            for k, v in d2.items():
                changed |= (k not in d1) or (d1[k] != v)
                d1[k] = v
            return changed

        return any( _update_changed(self.data, handler.get(data))
                    for handler in self.handlers )

    def update(self):
        """
        Fetch the device status from the bridge and parse the JSON object.
        """
        data = self.bridge._execute('{:}/{:}'.format(self.kind, self.index))
        return self.parse(data)


"""
Device helper classes below.

All classes must provice five methods:
* `is_applicable`: Determine whether they are responsible for a given JSON object.
* `values`: A list of values returned by this class.
* `parameters`: A list of configurable parameters (should be a subset of `values`).
* `get`: Parse the given JSON into a simple dict of `values`.
* `set`: Change the properties listed in `parameters`.
"""


class HueGeneric(object):
    """
    Basic properties that any Hue device must provide:
    Connection status, manufacturer, model and name.
    The `name` can be changed.
    """

    @classmethod
    def is_applicable(cls, data):
        return (data['manufacturername'] == 'Philips') \
           and (data['type'] in [ 'ZLLLightLevel', 'ZLLPresence', 'ZLLSwitch', 'ZLLTemperature',
                                  'Color temperature light' ])

    @classmethod
    def values(cls):
        return [ 'reachable', 'device_name', 'device_model', 'name' ]

    @classmethod
    def parameters(cls):
        return [ 'name' ]

    def get(self, data):
        return { 'reachable':    data['config']['reachable'] if 'reachable' in data['config'] else 
                                 data['state']['reachable'],
                 'device_name':  data['manufacturername'] + ' ' + data['productname'],
                 'device_model': data['modelid'],
                 'name':         data['name'] }

    def set(self, name=None, **kwargs):
        return { '': { 'name': name } }


class HueLight(object):
    """
    Generic hue light.
    Can be turned on, or dimmed.
    """

    @classmethod
    def is_applicable(cls, data):
        # TODO: Add other device types here (e.g. plain bulbs, and color bulbs).
        return (data['type'] in [ 'Color temperature light' ])

    @classmethod
    def values(cls):
        return [ 'on', 'brightness', 'alert' ]

    @classmethod
    def parameters(cls):
        return [ 'on', 'brightness', 'alert' ]

    def get(self, data):
        return { 'on':         data['state']['on'],
                 'brightness': data['state']['bri'] / 254.,
                 'alert':      data['state']['alert'], }

    def set(self, on=None, brightness=None, alert=None, **kwargs):
        if (brightness is not None) and ((brightness < 0) or (brightness > 1)):
            logger.error('Brightness {:} is out of range (0..254)!'.format(brightness))

        return { 'state': { 'on':    on,
                            'bri':   int(brightness * 254),
                            'alert': alert } }


class HueColorTemperature(object):
    """
    White ambiance hue light, with adjustable color temperature.
    """

    @classmethod
    def is_applicable(cls, data):
        return (data['type'] in [ 'Color temperature light' ])

    @classmethod
    def values(cls):
        return [ 'colortemp' ]

    @classmethod
    def parameters(cls):
        return [ '' ]

    def get(self, data):
        self._ct_min = data['capabilities']['control']['ct']['min']
        self._ct_max = data['capabilities']['control']['ct']['max']
        return { 'colortemp': int(1000000 / data['state']['ct']) }

    def set(self, colortemp=None, **kwargs):
        state_change = dict()

        if colortemp is not None:
            mirad = int(1000000 / colortemp)
            if (mirad < self._ct_min) or (mirad > self._ct_max):
                logger.error('Color temperature {:} is out of range ({:}..{:})!'.format(
                             mirad, self._ct_min, self._ct_max))

            state_change['ct'] = mirad

        return { 'state': state_change }


class HueBatteryStatus(object):
    """
    Tracks the battery status of battery-powered devices.
    """

    @classmethod
    def is_applicable(cls, data):
        return (data['manufacturername'] == 'Philips') \
           and ('battery' in data['config'])

    @classmethod
    def values(cls):
        return [ 'battery' ]

    @classmethod
    def parameters(cls):
        return [ ]

    def get(self, data):
        return { 'battery': data['config']['battery'] }

    def set(self, **kwargs):
        return { }


class HueTemperatureSensor(object):
    """
    Temperature sensor, e.g. in the Hue motion sensor.
    """

    @classmethod
    def is_applicable(cls, data):
        return (data['manufacturername'] == 'Philips') \
           and ('temperature' in data.get('state', {}))

    @classmethod
    def values(cls):
        return [ 'temperature', 'lastupdated' ]

    @classmethod
    def parameters(cls):
        return [ ]

    def get(self, data):
        return { 'temperature': data['state']['temperature']/100.,
                 'lastupdated': data['state']['lastupdated'] }

    def set(self, **kwargs):
        return { }


class HueLightSensor(object):
    """
    Light level sensor, e.g. in the Hue motion sensor.

    Also provides `dark` and `daylight` properties,
    which are defined as follows:
    * `dark`: `lightlevel` < `tholddark`
    * `daylight`: `lightlevel` > `tholddark` + `tholdoffset`

    The values for `tholddark` and `tholdoffset` can be changed.
    """

    @classmethod
    def is_applicable(cls, data):
        return (data['manufacturername'] == 'Philips') \
           and (data['type'] == 'ZLLLightLevel')

    @classmethod
    def values(cls):
        return [ 'lightlevel', 'lastupdated', 'dark', 'daylight' ]

    @classmethod
    def parameters(cls):
        return [ 'tholddark', 'tholdoffset' ]

    def get(self, data):
        return { 'lightlevel':  data['state']['lightlevel'],
                 'dark':        data['state']['dark'],
                 'daylight':    data['state']['daylight'],
                 'lastupdated': data['state']['lastupdated'] }

    def set(self, tholddark=None, tholdoffset=None, **kwargs):
        return { 'config': { 'tholddark':   tholddark,
                             'tholdoffset': tholdoffset } }


class HuePresenceSensor(object):
    """
    Hue motion sensor.

    The sensitivity can be changed (usually 0/1/2).
    """

    @classmethod
    def is_applicable(cls, data):
        return (data['manufacturername'] == 'Philips') \
           and (data['type'] == 'ZLLPresence')

    @classmethod
    def values(cls):
        return [ 'presence', 'lastupdated' ]

    @classmethod
    def parameters(cls):
        return [ 'sensitivity', 'ledindication' ]

    def get(self, data):
        self._sensitivity_max = data['config']['sensitivitymax']
        return { 'presence':    data['state']['presence'],
                 'lastupdated': data['state']['lastupdated'] }

    def set(self, sensitivity=None, ledindication=None, **kwargs):
        if (sensitivity is not None) and (sensitivity > self._sensitivity_max):
            logger.error('Desired sensitivity {:} is larger than device maximum {:}!'.format(
                         sensitivity, self._sensitivity_max))

        return { 'config': { 'sensitivity':   sensitivity,
                             'ledindication': ledindication, } }


class HueDimmerSwitch(object):
    """
    Dimmer switch with (four) buttons.
    """

    @classmethod
    def is_applicable(cls, data):
        return (data['manufacturername'] == 'Philips') \
           and (data['type'] == 'ZLLSwitch')

    @classmethod
    def values(cls):
        return [ 'buttonevent', 'lastupdated' ]

    @classmethod
    def parameters(cls):
        return [ ]

    def get(self, data):
        return { 'buttonevent': data['state']['buttonevent'],
                 'lastupdated': data['state']['lastupdated'] }

    def set(self, **kwargs):
        return { }

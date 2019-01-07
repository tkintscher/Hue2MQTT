#!/usr/bin/env python3
import argparse
import configparser
import json
import logging
import sys
import time

import paho.mqtt.client as mqtt

import hue



# configure logging
logging.basicConfig(level=logging.INFO,
                    format='[%(asctime)s] %(levelname)-7s %(name)-12s -- %(message)s',
                    datefmt='%Y/%m/%d %H:%M:%S')
main_logger = logging.getLogger('MAIN')
mqtt_logger = logging.getLogger('MQTT')

# parse command line arguments
parser = argparse.ArgumentParser()
parser.add_argument('--config', default='config.txt',     help='Configuration file')
parser.add_argument('--poll',   default=0.1, type=float,  help='Polling interval (seconds)')
args = parser.parse_args()

# read config file
config = configparser.ConfigParser()
config.read(args.config)

# register with Hue bridge if no API key is present
if 'auth' not in config.options('Hue'):
    auth_key = hue.Bridge.register(config['Hue']['Host'])
    if auth_key is None:
        sys.exit(1)
    config.set('Hue', 'Auth', auth_key)
    with open(args.config, 'w') as f:
        config.write(f)

# connect to bridge
bridge = hue.Bridge(config['Hue']['Host'], config['Hue']['Auth'])

# set up MQTT
mqtt_prefix = config['MQTT']['Prefix']

def on_connect(client, userdata, flags, rc):
    mqtt_logger.info('Connected!')

def on_disconnect(client, userdata, rc):
    mqtt_logger.info('Disconnected!')
    sys.exit(1)

def on_publish(client, userdata, mid):
    mqtt_logger.debug('Message {:} published!'.format(mid))

def on_message(client, userdata, message):
    prefix, dtype, uid, action = message.topic.split('/')

    if action == 'set':
        payload = json.loads(message.payload)
        device  = userdata.by_uid(uid)
        device.set(**payload)

    elif action == 'update':
        device = userdata.by_uid(uid)
        device.update()
        client.publish('/'.join((prefix, dtype, uid)), json.dumps(device.data, indent=2), 0, True)

client = mqtt.Client(config['MQTT']['Client'], clean_session=False, userdata=bridge)
client.enable_logger(mqtt_logger)
client.on_connect    = on_connect
client.on_disconnect = on_disconnect
client.on_publish    = on_publish
client.on_message    = on_message
if config.getboolean('MQTT', 'TLS'):
    client.tls_set()
client.connect(config['MQTT']['Host'], port=int(config['MQTT']['Port']), keepalive=60)

# publish current state for each device
for device in bridge:
    path = '{:}/{:}/{:}'.format(mqtt_prefix, device.kind, device.uid)
    device.update()
    main_logger.debug('%s -> %s', path, device.data)
    client.publish(path, json.dumps(device.data, indent=2), 0, True)

# subscribe to external settings
client.subscribe('{:}/+/+/set'.format(mqtt_prefix))
client.subscribe('{:}/+/+/update'.format(mqtt_prefix))

# main loop
while True:
    time.sleep(0.1)

    # update sensor and light status
    changed_devices = bridge.update('sensors') + bridge.update('lights')

    # publish changes
    for device in changed_devices:
        path = '{:}/{:}/{:}'.format(mqtt_prefix, device.kind, device.uid)
        main_logger.info('%s -> %s', path, device.data)
        client.publish(path, json.dumps(device.data, indent=2), 0, True)

    # process mqtt messages
    client.loop()

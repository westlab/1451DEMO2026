#!/usr/bin/python
import paho.mqtt.client as mqtt
from time import sleep

# when connected with broker
def on_connect(client, userdata, flag, rc):
  print("Connected with result code " + str(rc))

# when disconnected with broker
def on_disconnect(client, userdata, rc):
  if rc != 0:
     print("Unexpected disconnection.")

# when publish is compleated
def on_publish(client, userdata, mid):
  print("publish: {0}".format(mid))

def main():
  client = mqtt.Client()                 # Create instance
  client.on_connect = on_connect         # Register callback functions
  client.on_disconnect = on_disconnect
  client.on_publish = on_publish
  client.connect("broker.hivemq.com", 1883, 60)
  client.loop_start()    # sub:loop_forever() / pub:loop_start()
  i = 0
  while(1):
    client.publish("OUCtest/yourID","Hello!"+str(i))
    i = i+1
    sleep(3)

if __name__ == '__main__':
  main()

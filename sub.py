#!/usr/bin/python
import paho.mqtt.client as mqtt
 
# when connected with broker
def on_connect(client, userdata, flag, rc):
  print("Connected with result code " + str(rc))  # 接続できた旨表示
  client.subscribe("OUCtest/yourID")  # subするトピックを設定 

# when disconnected with broker
def on_disconnect(client, userdata, rc):
  if  rc != 0:
    print("Unexpected disconnection.")

# when received messages
def on_message(client, userdata, msg):
  print("Received message '" + str(msg.payload) + "' on topic '" + msg.topic + "' with QoS " + str(msg.qos))

client = mqtt.Client()                 # create instance
client.on_connect = on_connect         # register callback functions
client.on_disconnect = on_disconnect
client.on_message = on_message
 
client.connect("broker.hivemq.com", 1883, 60)
client.loop_forever()

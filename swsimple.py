import time

import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)

GPIO.setup(8, GPIO.IN, pull_up_down=GPIO.PUD_UP)

while True:
    GPIO.wait_for_edge(8, GPIO.FALLING) # wait for press

    print('SW ON')

    time.sleep(0.3) # Chatterring proof

import time

import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)

GPIO.setup(8, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(24, GPIO.OUT)

while True:
    sw = GPIO.input(8)
    if 0==sw:
        print('SW ON')
        GPIO.output(24, 1)    # LED ON
    else:
        print('SW OFF')
        GPIO.output(24, 0)    # LED OFF

    time.sleep(0.3) # Chatterring proof

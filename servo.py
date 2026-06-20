import time

#GPIO initialization
import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)

#GPIO4 initialization for output
GPIO.setup(4, GPIO.OUT)

#GPIO4i initialization for PWM@50Hz
p = GPIO.PWM(4, 50)

#Duty Cycle 0%
p.start(0.0)

while True:
    print("input Duty Cyle (2.5 - 12)")
    dc = float(input())

    #DutyCycle dc%
    p.ChangeDutyCycle(dc)

    #Set max 180 degree and wait more than 0.3sec
    time.sleep(0.4)

    #Set DutyCycle0% when moved
    p.ChangeDutyCycle(0.0)

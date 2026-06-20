# -*- coding: utf-8 -*-
import time
import RPi.GPIO as GPIO

C = 0.01  # 0.1uF
R1 = 330  # protection registance R1=1k ohm
E = 3.3  # Voltage
V = 1.65

GPIO.setmode(GPIO.BCM)

a_pin = 18  # GPIO18
b_pin = 23  # GPIO23


def discharge():
    GPIO.setup(a_pin, GPIO.IN)
    GPIO.setup(b_pin, GPIO.OUT)
    GPIO.output(b_pin, False)  # Discharge C
    time.sleep(0.1)  # wait for discharging


def charge_time():
    GPIO.setup(b_pin, GPIO.IN)  # input
    GPIO.setup(a_pin, GPIO.OUT)  # output
    GPIO.output(a_pin, True)  # charge C
    t1 = time.time()
    while not GPIO.input(b_pin):  # measure HIGH
        pass
    t2 = time.time()
    return (t2 - t1) * 1000000  # change time micro


def analog_read():
    discharge()
    t = charge_time()
    # print("{}μS".format(t))
    discharge()
    return t


def read_resistance():
    n = 3
    total = 0
    for i in range(1, n):  # mesure 3 times
        total = total + analog_read()
    t = total / float(n)
    r = ((E * t) / (C * V)) - R1  # R=T/C
    # T = 0.632 * t * E
    # r = (T / C) - R1
    return r


try:
    while True:
        print("{:.1f}Ω".format(read_resistance()))
        time.sleep(0.5)
finally:
    GPIO.cleanup()

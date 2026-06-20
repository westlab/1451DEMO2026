#coding:utf-8

import RPi.GPIO as GPIO
import time
import math

pin = 12   # buzzer pin
#a0=27.500   #a0 sound
a0=227.500

#n-th note frequency
def onkai(n):
    return a0*math.pow(math.pow(2,1/12),n)

# frecuency for notes
DO=onkai(27)
RE=onkai(29)
MI=onkai(31)
SO=onkai(34)

# merody and rythms
mery_merody=[MI,RE,DO,RE,MI,MI,MI,RE,RE,RE,MI,SO,SO,MI,RE,DO,RE,MI,MI,MI,RE,RE,MI,RE,DO]
mery_rhythm=[0.9,0.3,0.6,0.6,0.6,0.6,1.2,0.6,0.6,1.2,0.6,0.6,1.2,0.9,0.3,0.6,0.6,0.6,0.6,1.2,0.6,0.6,0.9,0.3,1.8]

GPIO.setmode(GPIO.BCM)
GPIO.setup(pin,GPIO.OUT,initial=GPIO.LOW)

p = GPIO.PWM(pin,1)
p.start(50)

# for stability
p.ChangeFrequency(2)
time.sleep(2)

# play according to the array
for i, oto in enumerate(mery_merody):
    p.start(50)
    p.ChangeFrequency(oto)
    time.sleep(mery_rhythm[i])
    p.stop()
    time.sleep(0.03)

p.stop()
GPIO.cleanup()

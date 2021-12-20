import pycom
import machine                  # for machine.idle when using wlan
from machine import Pin, I2C    # To control the pin that RESETs the ESP32-CAM, I2C for RTC
from machine import UART        # Receiving pictures from the ESP32-CAM
from machine import ADC         # Battery voltage measurement
from network import WLAN        # Connecting with the WiFi; Will not be needed when connecting with LTE
from network import LTE         # Connect to network using LTE
import base64                   # For encoding the picture
import urequests as requests    # Used for http transfer with the server
import utime                    # Time delays
import usocket as socket
from socket import AF_INET, SOCK_DGRAM, SOCK_STREAM
import ustruct
from urtc import DS3231         # DS3231 real time clock

from _pybytes import Pybytes
from _pybytes_config import PybytesConfig

pycom.heartbeat(False)

# Assign the Station ID number (0-99)
station_id = 50

timezone = -5 # est: -5   edt: -4

# global LTE object
lte = LTE()
#print(lte.imei())  # Print the GPY IMEI
#print(lte.iccid())  # Print the SIM Card ICCID

# Establish the WiFi object as a station; External antenna
wlan = WLAN(mode=WLAN.STA,antenna=WLAN.EXT_ANT,max_tx_pwr=30)  #range is 8 to 78

i2c = I2C(0, I2C.MASTER, baudrate=100000)  # use default pins P9 and P10 for I2C
ds3231 = DS3231(i2c)

# Define uart for UART1.  This is the UART that
#    receives data from the ESP32-CAM
#    For now, the ESP32-CAM transmits to the GPy at 38400 bps.  This can probably be increased.
uart = UART(1, baudrate=38400)


# Define the trigger pin for waking up the ESP32-CAM
#    When this pin is pulled LOW for approx 1ms
#    and released to float HIGH, the ESP32-CAM
#    will wake up, take a picture, save the picture
#    to its on-board SD card and then transmit the
#    picture over the UART to this Gpy
# Make sure the trigger is pulled HIGH
#    For the OPEN_DRAIN mode, a value of 0 pulls
#    the pin LOW and a value of 1 allows the
#    pin to float at the pull-up level
#    The ESP32-CAM RESET pin is internally pulled up.
camera_trigger = Pin('P8', mode=Pin.OPEN_DRAIN, value=1)

# Define the pin to RESET the GPy.
gpy_reset_trigger = Pin('P23', mode=Pin.OPEN_DRAIN, value=1)

# Define the pin to enable the battery voltage divider
#   When the voltage divider is enabled the voltage can be measured
#   When the voltage divider is disabled, current flow through the
#      resistor divider is cut off - helps conserve battery energy.
#   Initialize the pin to disable the voltage divider
gpy_enable_vmeas = Pin('P19', mode=Pin.OUT)
gpy_enable_vmeas.value(0)




############################################################
############ Begin function definitions ####################
def set_next_alarm():
    startup_datetime = ds3231.datetime()   # Get the time from the DS3231 RTC on startup

    startup_minute = startup_datetime[5]
    startup_hour = startup_datetime[4]
    startup_day = startup_datetime[2]
    startup_year = startup_datetime[0]

    print("startup time: ", startup_datetime)


    sequence_start = 0
    alarm_interval = 4    # number of hours between DS3231 interrupts

    #next_hour = (startup_hour[4] // alarm_interval) * alarm_interval + alarm_interval + sequence_start
    next_hour = (startup_hour // alarm_interval) * alarm_interval + alarm_interval + sequence_start
    next_hour = next_hour % 24
    next_minute = 5

    #  Calculate the time (GMT) for the next alarm based on the startup time.
    #  For this case, the alarm times are 0705, 1305, 1905 and 0105 hrs.

    # TODO: Finalize the interrupt interval prior to deploying.
    """
    if startup_hour >= 0 and startup_hour < 7:
        next_hour = 7
    elif startup_hour >= 7 and startup_hour < 13:
        next_hour = 13
    elif startup_hour >= 13 and startup_hour < 19:
        next_hour = 19
    else:
        next_hour = 1


    next_minute = 5
    """

    """
    # Testing.  Set the alarm to interrupt every 30 minutes.  
    # Format: [year, month, day, weekday, hour, minute, second, millisecond]
    next_hour = startup_hour
    next_minute = startup_minute + 15  # Interrupt every 30 minutes
    if next_minute > 59:
        next_minute -= 60
        next_hour += 1
        if next_hour > 23:
            next_hour -= 24
    """

    alarm = [None, None, None, None, next_hour, next_minute, 0, None]  # Alarm when hours, minutes and seconds (0) match
    alarm_datetime = tuple(alarm)
    ds3231.alarm_time(alarm_datetime)

    print("Next Alarm Time: ", ds3231.alarm_time())     # For debugging, print the alarm time
    ds3231.no_interrupt()               # Ensure both alarm interrupts are disabled
    ds3231.no_alarmflag()               # Ensure both alarm flags in the status register are clear (even though Alarm 2 is not used)
    ds3231.interrupt(alarm=0)           # Enable Alarm 1 (alarm=0) interrupt

    return startup_datetime




def connect_to_wifi():
    try:
        wlan.connect(ssid='JRG Guest', auth=(WLAN.WPA2, '600guest'), timeout=10000)
        while not wlan.isconnected():
            machine.idle()

    except Exception as e:
        print("Exception in connect_to_wifi()")
        print(e)
        print("Shutting down...")
        shutdown()    

    print("Connected to wifi: ", wlan.ifconfig())


def attach_to_lte():
    return_val = 0  # Initialize as 'Failed to attach'

    # First, enable the module radio functionality and attach to the LTE network
    try:
        attach_try = 1
        while attach_try < 5:
            lte.attach(apn="wireless.dish.com",type=LTE.IP)  # Ting using T-Mobile
            print("attaching..",end='')

            attempt = 0
            while attempt < 15:
                if not lte.isattached():
                    print(lte.send_at_cmd('AT!="fsm"'))         # get the System FSM
                    attempt += 1
                    utime.sleep(5.0)
                else:
                    print("attached!")
                    break
            # Break out of the 'attach_try' while loop if attached to the LTE network
            if lte.isattached():
                return_val = 1    # update return_val to indicate successful attach
                break
            else:
                print("Attempt #%d failed. Try attaching again!" %(attach_try))
                attach_try += 1.0
                utime.sleep(10)
    except Exception as e:
        print("Exception in s.sendall()")
        print(e)

    # If the GPy failed to connect to the LTE network return an error code
    if not lte.isattached():
        print("Failed to attach to the LTE system")
    
    return return_val


def connect_to_lte_data():
    return_val = 0
    # Once the GPy is attached to the LTE network, start a data session using lte.connect()
    connect_try = 0
    while connect_try < 10:
        lte.connect()
        print("connecting [##",end='')

        # Check for a data connection
        attempt = 0
        while attempt < 10:
            if not lte.isconnected():
                #print(lte.send_at_cmd('AT!="showphy"'))
                #print(lte.send_at_cmd('AT!="fsm"'))
                print('#',end='')
                attempt += 1
                utime.sleep(1.0)

            # Break out of the 'attempt' while loop if a data connection is established
            else:
                print("] connected!")
                break

        # If no data connection, disconnect and try again
        # If connected, update return_val and break out of the 'connect_try' while loop
        if lte.isconnected():
            return_val = 1         # update return_val to indicate successful data connection
            break
        else:
            print("Try the data connection again!")
            connect_try += 1.0
            utime.sleep(5)

    # If a data connection is not established, detach from the LTE network before returning
    if not lte.isconnected():
        print("Failed to connect to the LTE data network")  
        lte.detach(reset=False)

    return return_val


def send_sms_msg(voltage, datetime):
    ################## Send SMS ################################
    phone_number = 7623204402

    year = datetime[0]
    month = datetime[1]
    day = datetime[2]
    hour = datetime[4]
    minute = datetime[5]

    dt_string = "{:4.0f}-{:02.0f}-{:02.0f} {:02.0f}{:02.0f}hrs (Z)"
    dt = dt_string.format(year,month,day,hour,minute)

    sms_at_cmd = "AT+SQNSMSSEND=\"{}\",\"Meter JRG{:05.0f} @ {} Voltage {:.2f}\""
    sms_message = sms_at_cmd.format(phone_number,station_id,dt,voltage)

    #print(sms_message)

    attach_to_lte()
    if lte.isattached():
        print('sending an sms', end=' '); ans=lte.send_at_cmd(sms_message).split('\r\n'); print(ans)
        #lte.detach()   # Do not detatch from LTE.  If the attach was
                        #   successful, stay attached to support the picture transfer
    else:
        print("Did not attach to the LTE system so did not send an sms")




def process_picture(picture_len_int):
    buf = bytearray(picture_len_int)
    mv = memoryview(buf)

    idx = 0
    while idx < picture_len_int:
        if uart.any():
            bytes_read = uart.readinto(mv[idx:])
            idx += bytes_read
            print('.', end='')

    # Print the index counter.  This is the number of bytes copied to the picture buffer
    print(idx)

    b64_picture_bytes = base64.b64encode(buf)

    del buf

    # Transmit the encoded image to the server
    data_file = "{\"voltage\": " + string_volts + ",\"base64File\": \"" +  b64_picture_bytes.decode('ascii') + "\", \"id\": " + str(station_id) + ", \"timeStamp\": \"" + time_stamp + "\"}" 

    
    # URL string.  Transmit the encoded image to the server
    url = "http://gaepd.janusresearch.com:8555/file/base64"
    #url = "http://198.13.81.244:8555/file/base64"
    #url = "http://water.roeber.dev:80/file/base64"

    # HTTP Header string
    headers = {
        'Content-Type': 'application/json',
    }

    print("Send the image")
    try:
        response = requests.post(url, headers=headers, data=data_file)
        print(response.text)  # Prints the return filename from the server in json format
    except Exception as e:
        print(e)

        # try once more
        utime.sleep(5)
        try:
            response = requests.post(url, headers=headers, data=data_file)
            print(response.text)  # Prints the return filename from the server in json format
        except Exception as e:
            print(e)   




def battery_voltage():
    gpy_enable_vmeas.value(1)  # enable the battery voltage divider
    adc = ADC(0)             # create an ADC object
    adc_vbat = adc.channel(pin='P18',attn=adc.ATTN_0DB)   # create an analog pin on P18

    print("Reading Battery Voltage...")

    adc_value = 0.0
    for y in range(0,10):
        utime.sleep_ms(10)
        reading = adc_vbat()
        adc_value += reading

    gpy_enable_vmeas.value(0)  # disable the battery voltage divider

    # Take average of the 10 readings
    adc_value = adc_value / 10
    print("ADC count = %d" %(adc_value))

    # GPy  has 1.1 V input range for ADC using ATTN_0DB

    # The battery pack maximum voltage is 2 * 3.65 = 7.3V.  Allow for a maximum of 8V.
    #   Use a voltage divider consisting of 352k and 56k resistors.
    #   V_measured = 0.1372549 * V_battery
    #   For V_battery = 8V, V_measured = 1.1V
    #   For V_battery = 7.3V, V_measured = 1.002V

    volts = adc_value * 0.001754703 + 0.544528802 
    return volts

# Set the clock with NTP date/time
def sync_clock():
    return_val = 0

    print("Setting the DS3231 RTC ...")
    print("   Connecting to ntp")
    host = "pool.ntp.org"
    port = 123
    buf = 1024
    address = socket.getaddrinfo(host,  port)[0][-1]
    msg = '\x1b' + 47 * '\0'
    msg = msg.encode()
    TIME1970 = 2208988800 # 1970-01-01 00:00:00

    # Try 5 times to connect ot the NTP server
    ntp_try = 0
    while ntp_try < 5:
        client = socket.socket(AF_INET, SOCK_DGRAM)
        bytes_sent = 0
        for _ in range(10):
            bytes_sent = client.sendto(msg, address)
            utime.sleep(1)
            if bytes_sent > 0:                       #Connection to NTP server successful if bytes are sent
                #print("Sent to NTP server")
                # Receive the NTP time into t.  Adjust t with the base time, TIME1970.
                msg, address = client.recvfrom(buf)
                t = ustruct.unpack("!12I", msg)[10]
                t -= TIME1970

                # Convert epoch time, t in GMT, to 8-tuple [yr, mo, mday, hr, min, sec, weekday, yearday]
                ntp_time = utime.localtime(t)

                print("ntp_time (GMT): ", ntp_time)  

                # Set DS3231 time using NTP time.  First, adjust the time tuple to match the
                #    format requried by the DS3231 driver
                a = list(ntp_time)

                del a[7]            # delete the yearday value
                a.insert(3, 1)      # insert 1 for the weekday value.  Any weekday value that is in range 1-7
                                    # is OK since this program does not use the weekday.

                localtime = tuple(a)
                ds3231.datetime(localtime)

                # The ds3231 date/time has been updated.  Now re-set the next alarm time
                set_next_alarm()

                return_val = 1    # Update the return_val to indicate time update success
                return return_val
            else:
                print("loop in time server")
                utime.sleep(5)
        ntp_try += 1
        client.close()
        print("Try NTP again")
        utime.sleep(5)            # Time delay before next attempt at connecting to the NTP server
    return return_val

def gpy_reset():
    # Pull the RESET pin LOW to reset the GPy
    gpy_reset_trigger.value(0)

# When the DS3231 RTC pulls the P22 LOW, this handler pulls the gpy_reset_trigger LOW.  The GPY is reset.
# Upon reset, the GPY clears the DS3231 interrupt request before configuring P22 as an interrupt source
# Explanation:
#    When the DS3231 time matches the alarm time (either Alarm 1 or 2) the DS3231 pulls the INT pin LOW and keeps is LOW until
#    the Alarm Flag in the DS3231 status register is cleared. For example, if the DS3231 time matched the Alarm 1 time, then A1F in the status
#    register must be cleared to allow the INT pin to go HIGH. 
#
#    For this reason, the DS3231 INT pin cannot be connected directly to the GPy RESET pin (P23).  The INT pin would keep the GPY in RESET
#    permanently since the DS3231 alarm flag could not be cleared.
#
#    Therefore, connect the DS3231 RESET tp P22.  When P22 detects an RTC reset, it in turn pulls P23 (gpy_reset_trigger) LOW
#    to reset the GPY.  On bootup, clear the RTC reset condition and then reconfigure P22 as an interrupt source.
def ds3231_int_handler(arg):
   gpy_reset()


#   For shutdown, put the GPy in a sleep mode for some time longer than the normal RTC delay.  For example, if the
#      RTC initiates a RESET every six hours, put the GPy in a sleep mode for 6hrs and 15 minutes.  If all else
#      fails, the GPy will reboot at the end of the software delay
def shutdown():
    # Delay.  Expect that the RTC will reset the GPY before this delay expires.
    #    Delay 6hrs and 15 minutes (22500 seconds) assuming that the RTC interrupts every 6 hours
    #machine.deepsleep(22500000)
    utime.sleep(22500)

    # For testing, transmit more frequently
    #utime.sleep(300)
    
    # Pull the RESET pin LOW to reset the GPy
    gpy_reset()

#########################################################
################ End function definitions ###############



################################################ Entry Point ############################################
# For testing only.  A message and a delay
print("Starting ...")
utime.sleep(2)

# Before any other action, set the next DS3231 alarm time and clear the DS3231 interrupt request.  If any of the functions hang,
#   the DS3231 will reset the GPy at the next alarm.
startup_datetime = set_next_alarm()

# Now that the DS3231 interrupt request is cleared, configure P22 as an interrupt pin to detect DS3231 interrupts.
ds3231_trigger = Pin('P22', mode=Pin.IN, pull=None)  # external pull up resistor on ds3231 reset pin
ds3231_trigger.callback(Pin.IRQ_FALLING, ds3231_int_handler)


######################## Read the battery voltage ##############################
volts = battery_voltage()
print("Voltage = %5.2f V" % (volts))

# Use voltage as a string without a decimal so that it can be included in the photo filename
rounded_volts = 100 * round(volts,2)  # e.g., for volts = 6.475324, rounded_value = 648.
integer_volts = int(rounded_volts)    # for rounded_value = 648.  integer_value = 648 
string_volts = str(integer_volts)


################# Send an SMS and/or a Pybytes message ###########################
# Read the nvram interval (or, interrupt) counter and take appropriate action.
#   The RTC interrupts every 30 minutes
int_count = 0

try:
    int_count = pycom.nvs_get('int_counter')
except Exception as e:
    print(e)

# This is for the initial condition in which the variable is not yet defined in nvram
if int_count is None:
    int_count = 0

print("Send an SMS and/or Pybytes message as needed")

# Send an SMS once every 3 intervals 
trigger = int_count % 3
if trigger == 0:
    print("Send SMS")
    send_sms_msg(volts, startup_datetime)

# Send a pybytes message every 2 intervals
trigger = int_count % 2
if trigger == 0:
    print("Call home to pybytes...")
    conf = PybytesConfig().read_config()
    pybytes = Pybytes(conf)
    if not pybytes.isconnected():
        try:
            print("Attempt to connect to pybytes")
            pybytes.connect()
            print("connected to pybytes")
        except Exception as e:
            print("Did not connect to pybytes: ", e)
    else:
        print("Already connected")

    if pybytes.isconnected():
        print("Sending data to pybytes")
        volt_string = "{:.2f}"
        v = volt_string.format(volts)
        try:
            pybytes.send_signal(0, v)  # Send voltage message to channel 0
        except Exception as e:
            print(e)
    # Disconnect from pybytes at the end of the program, not here


# Increment the counter.
int_count += 1
pycom.nvs_set('int_counter', int_count)
print("Interval counter: ", int_count)


#################################### Network Connection #############################################################
print("Connecting to the network")
#connect_to_wifi()

# The modem my already be attached to the lte network from a previous SMS or Pybytes transmission

if not lte.isattached():
    attach_to_lte()

if not lte.isattached():
    print("Shutting down.  Better luck next reset.")
    shutdown()     # Wait for the next scheduled reset

connect_to_lte_data()

if not lte.isconnected():
    print("Did not connect to lte data.  Shutting down...")
    shutdown()      # Wait for the next scheduled reset




################################### DS3231 Synchronization with NTP server ##################################################
# Synchronize the DS3231 clock with NTP on the first day of the month
#   or if the year is wrong (usually on first start or backup battery is replaced)
#   startup_datetime[0]  - year
#   startup_datetime[2]  - day
#   startup_datetime[4]  - hour
#   startup_datetime[5]  - minute
#  Note: sync_clock() also updates the next alarm time
if(startup_datetime[0] < 2021 or startup_datetime[2] == 1):
    sync_clock()
else:
    print("DS3231: no update needed")

# DS3231 time:
# datetime[0] year
# datetime[1] month
# datetime[2] date
# datetime[3] weekday
# datetime[4] hour
# datetime[5] minute
# datetime[6] second
datetime = ds3231.datetime()
print('DS3231 time:', ds3231.datetime())

time_stamp = '{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}'.format(datetime[0], datetime[1], datetime[2], datetime[4], datetime[5], datetime[6])
camera_time_stamp = '{:04d}{:02d}{:02d}{:02d}{:02d}'.format(datetime[0], datetime[1], datetime[2], datetime[4], datetime[5])
#print("Timestamp", time_stamp)
#print("CameraTimestamp", camera_time_stamp)

# Picture filename.  Transmit this to the ESP32-CAM. It is used for the SD Card filename on the ESP32-CAM
picture_filename = str(station_id) + '_' + camera_time_stamp + '_' + string_volts + '\0'
print(picture_filename)  # Print the filename to make sure it is properly formatted

# For testing only.  Print a string to the GPy terminal
print('new picture')

# Parse through the data that follows the ESP32-CAM bootup transmission to find the keyword, 'ready'

# Transmit 'Hello' until 'ready' is received
keyword = b'ready'  # Expected word from the ESP32-CAM
utime.sleep(1)

camera_connect = 0
while camera_connect < 3:
    print("Camera connect attempt")
    print(camera_connect + 1)

    # Toggle the ESP32-CAM RESET line to initiate the picture capture process
    camera_trigger(0)
    utime.sleep_ms(10)
    camera_trigger(1)

    # Send a greeting followed by reading the reply
    reply_count = 0
    while reply_count < 50:
        uart.write('Hello\0')
        utime.sleep_ms(200)
        reply = uart.readline()
        print(reply)
        if reply == keyword:
                print("found the keyword")  # The word 'ready' was received
                break
        reply_count += 1
    print("Completed attempt to find the keyword")
    if reply == keyword:
        break
    camera_connect += 1
    utime.sleep(5)

if reply == keyword:
    print("send the picture filename")
else:
    print("The camera did not connect.  Shutting down...")
    shutdown()

# Send the picture filename to the ESP32-CAM.  This filename will be used
#   by the ESP32-CAM to store the picture to its local SD-Card.

utime.sleep_ms(200)
uart.write(picture_filename)



# Read the picture length from the ESP32-Cam.  Convert the value to an integer
picture_len_try = 0
received_picture_len = False
while picture_len_try < 50:
    picture_len = uart.readline()
    try:
        # Strip the trailing whitespace (e.g. \r\n)
        picture_len_bytes = picture_len.strip()
        # Cast the value to an integer.  If the case is successful, the picture length is a number
        picture_len_int = int(picture_len_bytes)
        received_picture_len = True
        break
    except:
        print('The picture length is not a number')
    picture_len_try += 1
    utime.sleep_ms(100)

if received_picture_len == False:
    shutdown()
else:
    print(picture_len_int)


print('Begin transfer')
process_picture(picture_len_int)

# Turn off the UART port
uart.deinit()

# For testing only.  Indicates that the picture processing (capture, encode, transmit) is complete
print('end transfer')



# pybytes is not defined for every picture transfer.  Use a try: structure to disconnect from pybytes
try:
    if pybytes.isconnected():
        print("Disconnect from pybytes")
        pybytes.disconnect()
except Exception as e:        
    print("pybytes.disconnect(): ", e)

# Picture transfer is complete so disconnect from the network
#wlan.disconnect()
lte.deinit(detach=True,reset=True)

print("Network disconnected, going to sleep")

shutdown()
import pycom
import machine
from machine import Pin, I2C    # To control the pin that RESETs the ESP32-CAM, I2C for RTC
from machine import RTC
from machine import UART        # Receiving pictures from the ESP32-CAM
from machine import ADC         # Battery voltage measurement
from network import WLAN        # Connecting with the WiFi; Will not be needed when connecting with LTE
from network import LTE         # Connect to network using LTE
import base64                   # For encoding the picture
import urequests as requests    # Used for http transfer with the server
import utime                    # Time delays
import socket
from socket import AF_INET, SOCK_DGRAM
import ustruct
from urtc import DS3231         # DS3231 real time clock

pycom.heartbeat(False)


# Assign the Station ID number (0-99)
station_id = "50"

# global LTE object
#lte = LTE()
#print(lte.imei())  # Print the GPY IMEI
#print(lte.iccid())  # Print the SIM Card ICCID

# For testing only - Use a WiFi object instead of an LTE object
# Establish the WiFi object as a station; External antenna
wlan = WLAN(mode=WLAN.STA,antenna=WLAN.INT_ANT,max_tx_pwr=20)  #range is 8 to 78

i2c = I2C(0, I2C.MASTER, baudrate=100000)  # use default pins P9 and P10 for I2C
ds3231 = DS3231(i2c)

# Define uart for UART1.  This is the UART that
#    receives data from the ESP32-CAM
#    For now, the ESP32-CAM transmits to the GPy at 38400 bps.  This can probably be increased.
uart = UART(1, baudrate=38400)

# Real time clock object
rtc = RTC()


# URL string.  Transmit the encoded image to the server
url = "http://gaepd.janusresearch.com:8555/file/base64"
#url = "http://water.roeber.dev:80/file/base64"

# HTTP Header string
headers = {
    'Content-Type': 'application/json',
}

# Real time clock time zone offset
est_timezone = -5   # Eastern standard time is GMT - 5
edt_timezone = -4   # Eastern daylight time is GMT - 4


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

def connect_to_wifi():
    wlan.connect(ssid='polaris', auth=(WLAN.WPA2, 'gALAtians_03:20'))
    #wlan.connect(ssid='JRG Guest', auth=(WLAN.WPA2, '600guest'), timeout=5000)
    while not wlan.isconnected():
        machine.idle()
    print(wlan.ifconfig())


def attach_to_lte():
    # Initialize the return value
    return_val = 0

    # First, enable the module radio functionality and attach to the LTE network
    attach_try = 0
    while attach_try < 3:
        lte.init() 
        #lte.attach(band=12, apn="wireless.dish.com")   # Specify band 12
        lte.attach(apn="wireless.dish.com",type=LTE.IP,legacyattach=True)  # Scan all bands
        print("attaching..",end='')

        attempt = 0
        while attempt < 20:
            if not lte.isattached():
                attempt += 1
                print('.',end='')
                print(lte.send_at_cmd('AT!="fsm"'))         # get the System FSM
                utime.sleep(2)
            else:
                print("attached!")
                break
        # Break out of the 'attach_try' while loop if attached to the LTE network
        if lte.isattached():
            return_val = 1    # update return_val to indicate successful attach
            break
        else:
            attach_try += 1.0
            lte.reset()   # Reset the cellular modem
            utime.sleep(7)  #Reset can take up to 5 seconds
            print("Attempt #%d failed. Try attaching again!" %(attach_try))
    
    # If the GPy failed to connect to the LTE network return an error code
    if not lte.isattached():
        print("Failed to connect to the LTE network")
    
    return return_val          # return_val is 0 (from initialization) to indicate the attach failed


def connect_to_lte_data():
    return_val = 0
    # Once the GPy is attached to the LTE network, start a data session using lte.connect()
    connect_try = 0
    while connect_try < 10:
        lte.connect()
        print("connecting [##",end='')

        # Check for a data connection
        attempt = 0
        while attempt < 100:
            if not lte.isconnected():
                attempt += 1
                utime.sleep(0.5)
                print('#',end='')
                #print(lte.send_at_cmd('AT!="showphy"'))
                #print(lte.send_at_cmd('AT!="fsm"'))

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
            connect_try += 1.0
            utime.sleep(5)
            print("Try the data connection again!")

    # If a data connection is not established, detach from the LTE network before returning
    if not lte.isconnected():    
        lte.detach(reset=False)

    return return_val

    #print(socket.getaddrinfo('pybytes.pycom.io', 80))  # For testing.  Uncomment this line to confirm the network socket call


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
    #data_file = "{\"base64File\": \"" +  b64_picture_bytes.decode('ascii') + "\", \"id\": " + station_id + ", \"timeStamp\": \"" + time_stamp + "\"}"
    data_file = "{\"voltage\": " + voltage_level + ",\"base64File\": \"" +  b64_picture_bytes.decode('ascii') + "\", \"id\": " + station_id + ", \"timeStamp\": \"" + time_stamp + "\"}" 

    # Post the picture using the uPython urequests library
    """
    try:
        wdt=machine.WDT(timeout=5*1000)
        response = requests.post(url, headers=headers, data=data_file)
        print(response.text)  # Prints the return filename from the server in json format
        response.close()
    except Exception as e:
        print(e)
    """



    # Post the picture using the uPython usockets library
    # Host at JRG, Inc
    host = "gaepd.janusresearch.com"
    port = 8555
    server_address = socket.getaddrinfo('gaepd.janusresearch.com', 8555)[0][-1]

    # Host on Digital Ocean
    #host = "water.roeber.dev"
    #port = 80
    #server_address = socket.getaddrinfo('water.roeber.dev', 80)[0][-1]


    headers = """\
POST /file/base64 HTTP/1.1\r
Content-Type: {content_type}\r
Content-Length: {content_length}\r
Host: {host}\r
\r\n"""

    header_bytes = headers.format(
        content_type="application/json",
        content_length=len(data_file),
        host=str(host) + ":" + str(port)
    ).encode('iso-8859-1')

    payload = header_bytes + data_file

    #print(server_address)
    #print(header_bytes)

    #s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s = socket.socket()
    s.setblocking(False)
    s.settimeout(15)

    print("Connect to server")
    s.connect(server_address)

    print("Ready to send")
    s.sendall(payload)
    print(s.recv(1024))  # Print the data that the server returns

"""
    s = socket.socket()
    s.setblocking(False)
    try:
        s.connect(server_address)
    except OSError as e:
        if str(e) == '119':   # For non-Blocking sockets 119 is EINPROGRESS
            print("Socket connection in progress")
        else:
            raise e
"""
    


def battery_voltage():
    gpy_enable_vmeas.value(1)  # enable the battery voltage divider
    adc = ADC(0)             # create an ADC object
    adc_vbat = adc.channel(pin='P18',attn=adc.ATTN_0DB)   # create an analog pin on P18

    print("")
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

    #volts = ((value * 1.1 ) / (4095.0 * 0.1372549))
    # should be: volts = ((value * 1.1) / 562.059), but the system is not calibrated
    # The following formula is used, instead
    #volts = ((adc_value * 1.1 ) / 575.0)
    volts = adc_value * 0.001754703 + 0.544528802 
    # volts = adc_value * 0.001885 + 3.908995
    print("Voltage = %5.2f V" % (volts))
    # Return voltage without a decimal
    rounded_value = 100 * round(volts,2)  # e.g., for volts = 6.475324, rounded_value = 648.
    integer_value = int(rounded_value)    # for rounded_value = 648.  integer_value = 648 
    return str(integer_value)

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

    # connect to server
    # TODO: Need a timeout to prevent hanging during NTP call
    ntp_try = 0
    while ntp_try < 5:
        client = socket.socket(AF_INET, SOCK_DGRAM)
        bytes_sent = 0
        for _ in range(10):
            bytes_sent = client.sendto(msg, address)
            utime.sleep(1)
            if bytes_sent > 0:                       #Connection to NTP server successful if bytes are sent
                #print("Sent to NTP server")
                # Receive the NTP time into t.  Adjust t with the base time, TIME1970
                msg, address = client.recvfrom(buf)
                t = ustruct.unpack("!12I", msg)[10]
                t -= TIME1970
                # adjust utime for the local timezone. By default, NTP time will be GMT
                utime.timezone(est_timezone*60**2)  # Calculate timezone using appropriate GMT offset
                # Convert epoch time, t, to 8-tuple [yr, mo, mday, hr, min, sec, weekday, yearday]
                ntp_time = utime.localtime(t)

                print("ntp_time (localtime): ", ntp_time)

                # Set DS3231 time using NTP time.  First, adjust the time tuple to match the
                #    format requried by the DS3231 driver
                a = list(ntp_time)

                del a[7]            # delete the yearday value
                a.insert(3, 1)      # insert 1 for the weekday value.  Any weekday value is in range 1-7
                                    # is OK since this program does not use the weekday.

                localtime = tuple(a)
                #print("Local time: ", localtime)
                ds3231.datetime(localtime)

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
# Upon reset, the GPY clears the DS3231 before configuring P22 as an interrupt source
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

    #For testing only - a short sleep time
    #utime.sleep(120)
    #machine.deepsleep(90000)   # 1000 * number of seconds.  For 1 second, deepsleep(1000)
    #utime.sleep(90)
    
    # Pull the RESET pin LOW to reset the GPy
    gpy_reset()

#########################################################
################ End function definitions ###############


################################################ Entry Point ############################################
# For testing only.  A message and a delay
print("Starting ...")
utime.sleep(1)

# Before any other action, set the next DS3231 alarm time.  If any of the functions hang,
#   the DS3231 will reset the GPy at the next alarm.  Even if the DS3231 starts with the wrong
#   clock time, set a future alarm time.

startup_datetime = ds3231.datetime()   # Get the time from the DS3231 RTC on startup

startup_minute = startup_datetime[5]
startup_hour = startup_datetime[4]
startup_day = startup_datetime[2]
startup_year = startup_datetime[0]

print("startup time: ", startup_datetime)
print("startup year: ", startup_year)
print("startup day: ", startup_day)
print("startup hour: ", startup_hour)
print("startup minute: ", startup_minute)

#  Calculate the time for the next alarm based on the startup time.
#  For this case, the alarm times are 0705, 1305, 1905 and 0105 hrs.
if startup_hour >= 0 and startup_hour < 7:
    next_hour = 7
elif startup_hour >= 7 and startup_hour < 13:
    next_hour = 13
elif startup_hour >= 13 and startup_hour < 19:
    next_hour = 19
else:
    next_hour = 1

next_minute = 5

# Testing.  Set the alarm
# Format: [year, month, day, weekday, hour, minute, second, millisecond]
#next_hour = startup_hour
#next_minute = startup_minute + 3  # Send a picture every three minutes
#if next_minute > 59:
#    next_minute -= 60
#    next_hour += 1
#    if next_hour > 23:
#        next_hour -= 24

alarm = [None, None, None, None, next_hour, next_minute, 0, None]  # Alarm when hours, minutes and seconds (0) match
alarm_datetime = tuple(alarm)
ds3231.alarm_time(alarm_datetime)

print("Next Alarm Time: ", ds3231.alarm_time())     # For debugging, print the alarm time
ds3231.no_interrupt()               # Ensure both alarm interrupts are disabled
ds3231.no_alarmflag()               # Ensure both alarm flags in the status register are clear (even though Alarm 2 is not used)
#ds3231.alarm(value=False, alarm=0)  # Clear the Alarm 1 (alarm=0) flag
#ds3231.alarm(value=False, alarm=1)  # Clear the Alarm 2 (alarm=1) flag even though Alarm 2 is not used
ds3231.interrupt(alarm=0)           # Enable Alarm 1 (alarm=0) interrupt


# Now that the DS3231 alarm flag is cleared, configure P22 as an
#   interrupt pin to detect DS3231 interrupts.
ds3231_trigger = Pin('P22', mode=Pin.IN, pull=None)  # external pull up resistor on ds3231 reset pin
ds3231_trigger.callback(Pin.IRQ_FALLING, ds3231_int_handler)


#################################### Network Connection #############################################################
connect_to_wifi()

"""
attached = 0
attached = attach_to_lte()

if not attached:
    print("Shutting down.  Better luck next reset.")
    shutdown()     # Wait for the next scheduled reset

connected = 0
connected = connect_to_lte_data()

if not connected:
    shutdown()      # Wait for the next scheduled reset
"""

################################### RTC Synchronization with NTP server ##################################################
# Synchronize the DS3231 clock with NTP on the first day of the month
#   or if the year is wrong (usually on first start or backup battery is discharged)
if(startup_year < 2021 or startup_year > 2025 or startup_day == 1):
    sync_clock()
else:
    print("DS3231 RTC does not need updating")


# Set the GPy software clock using the DS3231 time.  Read the DS3231 and adjust the
#   tuple to the format required by RTC()
ds3231_time = ds3231.datetime()
a = list(ds3231_time)
del a[7]            # ds3231.datetime() returns 'None' as a[7].  Delete it
del a[3]            # delete the weekday value
a.append(0)         # Append 0 in the microseconds position

localtime = tuple(a)
rtc.init(localtime)

print('DS3231 time:', ds3231.datetime())
print('RTC time:   ', rtc.now())



# The GPy software RTC is set using the DS3231 time.  Now verify the alarm time for next GPy reset
#   If the rtc year and the startup_year are the same, the next alarm is properly set
#   If the rtc year and startup_year are different, set the alarm using the correct time
current_datetime = rtc.now()
current_hour = current_datetime[3]
current_year = current_datetime[0]


if current_year != startup_year:
    if current_hour >= 0 and current_hour < 7:
        next_hour = 7
    elif current_hour >= 7 and current_hour < 13:
        next_hour = 13
    elif current_hour >= 13 and current_hour < 19:
        next_hour = 19
    else:
        next_hour = 1

    # set the alarm
    # (year, month, day, weekday, hour, minute, second, millisecond)
    alarm = [None, None, None, None, next_hour, next_minute, 0, None]  # Alarm when hours, minutes and seconds match

    alarm_datetime = tuple(alarm)
    ds3231.alarm_time(alarm_datetime)

    print("Next Alarm Time: ", ds3231.alarm_time())          # For debugging, print the alarm time
    ds3231.alarm(value=False, alarm=0)  # Clear the alarm flag
    ds3231.interrupt(alarm=0)           # Enable the interrupt


time_stamp = '{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}'.format(current_datetime[0], current_datetime[1], current_datetime[2], current_datetime[3], current_datetime[4], current_datetime[5])
camera_time_stamp = '{:04d}{:02d}{:02d}{:02d}{:02d}'.format(current_datetime[0], current_datetime[1], current_datetime[2], current_datetime[3], current_datetime[4])

# Read the battery voltage
voltage_level = battery_voltage()

# Picture filename.  Transmit this to the ESP32-CAM. It is used for the SD Card filename on the ESP32-CAM
picture_filename = station_id + '_' + camera_time_stamp + '_' + voltage_level + '\0'
#print("Timestamp", time_stamp)
print(picture_filename)  # Print the filename to make sure it is properly formatted


# Toggle the ESP32-CAM RESET line to initiate the picture capture process
camera_trigger(0)
utime.sleep_ms(10)
camera_trigger(1)


# For testing only.  Print a string to the GPy terminal
print('new picture')


# Parse through the data that follows the ESP32-CAM bootup
#    transmission for the keyword
# TODO: This needs a timeout escape so that the code does not hang here

# Transmit 'Hello' until 'ready' is received
keyword = b'ready'  # Expected word from the ESP32-CAM
utime.sleep(1)
# Send a greeting followed by reading the response
while True:
    uart.write('Hello\0')
    utime.sleep_ms(200)
    reply = uart.readline()
    print(reply)
    if reply == keyword:
            break

print("found the keyword")  # The word 'ready' was received

# Send the picture filename to the ESP32-CAM.  This filename will be used
#   by the ESP32-CAM to store the picture to its local SD-Card.
utime.sleep_ms(200)
uart.write(picture_filename)



# Read the picture length from the ESP32-Cam.  Convert the value to an integer
while True:
    picture_len = uart.readline()
    if(picture_len is not None):
        #print(picture_len)
        break


# Strip the trailing whitespace (e.g. \r\n)
picture_len_bytes = picture_len.strip()

# Cast the value to an integer
picture_len_int = int(picture_len_bytes)
print(picture_len_int)

print('Begin transfer')

process_picture(picture_len_int)

# Turn off the UART port
uart.deinit()

# For testing only.  Indicates that the picture processing (capture, encode, transmit) is complete
print('end transfer')



# Picture transfer is complete so disconnect from the network
wlan.disconnect()
#lte.deinit(detach=True,reset=False)

print("Network disconnected, going to sleep")

shutdown()
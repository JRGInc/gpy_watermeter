import pycom
import machine                  # for machine.idle when using wlan
from machine import Pin, I2C    # To control the pin that RESETs the ESP32-CAM, I2C for RTC
from machine import UART        # Receiving pictures from the ESP32-CAM
from machine import ADC         # Battery voltage measurement
from machine import WDT         # Watch dog timer
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


# global LTE object
try:
    # Wake up once per 4 hrs, do some processing (18 minutes), deepsleep for 3 hrs, 42 min.
    #   During the sleep the modem will go into a low power stat but staty attached to the network.
    lte = LTE(psm_period_value=4, psm_period_unit=LTE.PSM_PERIOD_1H,
          psm_active_value=3, psm_active_unit=LTE.PSM_ACTIVE_6M )
    print("lte power saving mode parameters: ", lte.psm())
except Exception as e:
    print("Create lte object error: ", e)

# Establish the WiFi object as a station; External antenna
#wlan = WLAN(mode=WLAN.STA,antenna=WLAN.EXT_ANT,max_tx_pwr=30)  #range is 8 to 78

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

    if startup_hour < 7 or startup_hour >= 19:
        next_hour = 7
    else:
        next_hour = 19

    # Arbitrary minute
    next_minute = 17


    alarm = [None, None, None, None, next_hour, next_minute, 0, None]  # Alarm when hours, minutes and seconds (0) match
    alarm_datetime = tuple(alarm)
    ds3231.alarm_time(alarm_datetime)

    print("Next Alarm Time: ", ds3231.alarm_time())
    ds3231.no_interrupt()               # Ensure both alarm interrupts are disabled
    ds3231.no_alarmflag()               # Ensure both alarm flags in the status register are clear (even though Alarm 2 is not used)
    ds3231.interrupt(alarm=0)           # Enable Alarm 1 (alarm=0) interrupt

    return startup_datetime


def attach_to_lte():
    return_val = 0  # Initialize as 'Failed to attach'

    # Attach to the LTE network
    max_trys = 5
    max_checks = 15
    try:
        attach_try = 1
        while attach_try <= max_trys:
            lte.attach(apn="wireless.dish.com",type=LTE.IP)  # Ting using T-Mobile
            print("attaching...")

            check = 0
            while check < max_checks:
                if not lte.isattached():
                    #print(lte.send_at_cmd('AT!="fsm"'))         # get the System FSM
                    check += 1
                    print("Attempt: ", attach_try, " Check: ",check," of ",max_checks, " checks")
                    utime.sleep(5.0)
                else:
                    print("attached!")
                    break
            # Break out of the 'attach_try' while loop if attached to the LTE network
            if lte.isattached():
                return_val = 1    # update return_val to indicate successful attach
                break
            else:
                if attach_try == max_trys:
                    print("Attempt #%d failed. Done." %(attach_try))
                else:
                    print("Attempt #%d failed. Try attaching again!" %(attach_try))
                attach_try += 1.0
                try:
                    lte.reset()
                except Exception as e:
                    print("lte reset error: ", e)
                utime.sleep(5)
    except Exception as e:
        print("LTE attach exception: ", e)
        print(e)

    # If the GPy failed to connect to the LTE network return an error code
    if not lte.isattached():
        print("Failed to attach to the LTE system")
    
    return return_val


def connect_to_lte_data():
    return_val = 0
    # Start a data session using lte.connect()
    connect_try = 0
    while connect_try < 10:
        try:
            lte.connect()
        except Exception as e:
            print("LTE connect() exception: ", e)
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


def send_sms_msg(station_id, voltage, datetime):
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

    try:
        print('sending an sms', end=' '); ans=lte.send_at_cmd(sms_message).split('\r\n'); print(ans)
    except Exception as e:
        print("SMS send failed: ", e)


def process_picture(picture_len_int, station_id, time_stamp):
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
        print(e, "Try sending once more")

        # try once more
        utime.sleep(5)
        try:
            response = requests.post(url, headers=headers, data=data_file)
            print(response.text)  # Prints the return filename from the server in json format
        except Exception as e:
            print(e, "Failed again")   




def battery_voltage():
    adc = ADC(0)             # create an ADC object
    adc_vbat = adc.channel(pin='P18',attn=adc.ATTN_0DB)   # create an analog pin on P18
    gpy_enable_vmeas.value(1)  # enable the battery voltage divider

    print("Reading Battery Voltage...")

    adc_value = 0.0
    for y in range(0,10):
        utime.sleep_ms(50)
        reading = adc_vbat()
        adc_value += reading

    gpy_enable_vmeas.value(0)  # disable the battery voltage divider

    # Take average of the 10 readings
    adc_value = adc_value / 10
    print("ADC count = %d" %(adc_value))

    # GPy  has 1.1 V input range for ADC using ATTN_0DB

    #volts = adc_value * 0.001754703 + 0.544528802
    volts = adc_value * 0.001686281 + 0.607095645 
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

def get_id():
    # Define a two digit station identification based on the LTE IMEI.  The station ID is used in the picture filename and
    #   is also part of the station name.
    #   In order to deploy identical code to all meter sensors, this lookup table containing all of the station IDs and
    #   IMEIs is needed.

    imei = lte.imei()

    if imei == '354347091116118':
        id = 29
    elif imei == '354347091855384':
        id = 30
    elif imei =='354347098256859':
        id = 31
    elif imei == '354347093248505':
        id = 32
    elif imei == '354347094696280':
        id = 33
    elif imei == '354347090353712':
        id = 34

    else:
        id = 10

    print("IMEI: ",imei," Station: ",id)

    return id


def transmit_picture(volts):
    # Attach to the LTE network.
    if not lte.isattached():
        print("Attach to the LTE network")
        attach_to_lte()

    # If the modem is still not attached to the LTE network, shut down
    if not lte.isattached():
        print("Shutting down.  Better luck next reset.")
        shutdown()     # Wait for the next scheduled reset


    # While attached to the LTE network and before making a data connection, execute commands that use AT modem calls
    #   If the LTE modem is already connected, suspend the PPP session while executing AT commands
    # Assign the Station ID number (0-99)

    if(lte.isconnected()):
        lte.pppsuspend()
        print("PPP session suspended")

    station_id = get_id()

    print("Send an SMS once a day")
    # Send a message if the current hour is between midnight and noon (Z) (between 1900 and 0700 EST)
    #   This is only executed if the interrupt is caused by the DS3231.
    print("Startup hour: ", startup_datetime[4])
    if startup_datetime[4] < 12:
    #if startup_datetime[4] >= 12:
        print("Sending an SMS notification")
        send_sms_msg(station_id, volts, startup_datetime)
    else:
        print("Not sending an SMS notification")

    try:
        lte.pppresume()
        print("PPP session resumed")
    except Exception as e:
        print("PPP session resume error: ", e)

    #print("For testing, send the SMS anyway...")
    #send_sms_msg(station_id, volts, startup_datetime)

    # Now make an LTE data connection
    if not lte.isconnected():
        print("Make LTE data connection")
        connect_to_lte_data()

    # If the modem still does not have a data connection, shut down
    if not lte.isconnected():
        print("Did not connect to lte data.  Shutting down...")
        shutdown()      # Wait for the next scheduled reset

    print("Connection to the network is complete")



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
        print("The camera did not connect.  Connect to pybytes...")
        connect_to_pybytes(volts)

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
    process_picture(picture_len_int, station_id, time_stamp)

    # For testing only.  Indicates that the picture processing (capture, encode, transmit) is complete
    print('end transfer')

    connect_to_pybytes(volts)

    

def gpy_reset():
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
    print("RTC interrupt occurred")
    utime.sleep(5)  # Sleep a few seconds so the print statement has time to transmit

    # Set an NVRAM flag indicating that a DS3231 interrupt occurred
    try:
        pycom.nvs_set("DS3231", 1)
    except Exception as e:
        print("Did not set the DS3231 key: ", e)
        utime.sleep(5)

    # Pull the RESET pin LOW to reset the GPy
    gpy_reset()


#   For shutdown, put the GPy in a sleep mode for some time longer than the normal RTC delay.  For example, if the
#      RTC initiates a RESET every twelve hours, put the GPy in deepsleep mode for 12hrs and 15 minutes.  If all else
#      fails, the GPy will reboot at the end of the software delay
def shutdown():
    uart.deinit()

    try:
        if lte.isattached():
            print("Keep the LTE modem active - do not deinit")
            #print("Deinit the LTE modem, stay attached")
            #lte.deinit(detach=True,reset=False)  # Call before deepsleep to save power
            #lte.deinit(detach=False,reset=False) # This does not save power but remains connected (attached?) to the LTE system
        else:
            print("LTE modem is detached")
    except Exception as e:
        print("Deinint LTE modem error: ", e)

    # Delay.  Expect that the RTC will reset the GPY before this delay expires.
    #print("... entering deepsleep")
    #machine.deepsleep(720 * 60 * 1000) # Deep sleep for 12hrs, 15 minutes (720 minutes)

    # Delay
    print("... enter sleep mode")
    utime.sleep(750 * 60) # sleep for 12hrs 30 minutes (Note: the DS3231 interrupts every 12 hrs so this sleep period should not expire)
    #machine.sleep(5 * 60000)
    #machine.deepsleep(5 * 60 * 10000)

    print("exit sleep")
    utime.sleep(3)
    
    # Pull the RESET pin LOW to reset the GPy
    gpy_reset()


def connect_to_pybytes(battery_voltage):
# Connect to Pybytes and send a signal
    conf = PybytesConfig().read_config()
    try:
        pybytes = Pybytes(conf)
    except Exception as e:
        print("Did not instantiate the pybytes object")

    if not pybytes.isconnected():
        print("Connect to Pybytes...")
        try:
            pybytes.start()
        except Exception as e:
            print("Did not connect to Pybytes, try once more")
            utime.sleep(10)

    # Try to connect a second time
    if not pybytes.isconnected():
        print("Try connecting to Pybytes a second time...")
        conf = PybytesConfig().read_config()
        pybytes = Pybytes(conf)
        try:
            pybytes.start()
        except Exception as e:
            print("Did not connect to Pybytes, shut down: ", e)


    if pybytes.isconnected():
        print("Sending data to pybytes")
        volt_string = "{:.2f}"
        v = volt_string.format(battery_voltage)
        print("Voltage value sent to Pybytes: ", v)
        pybytes.send_signal(0, v)
        utime.sleep(3)

        # Set the watchdog timer - overrides the bootup Pybytes watchdog setting (Could disable the Pybytes watchdog)
        #print("Set the watchdog timer")
        wdt = WDT(timeout=1440 * 60 * 1000)  # 60*1000=1minute; 24 hour watchdog timeout
        wdt.feed()
    else:
        # If not connected to pybytes, deinit the modem.  Sleep until the next time the RTC interrupts.
        print("Network disconnected, going to sleep")

    print("Going to sleep")
    utime.sleep(3)

    shutdown()

 

#########################################################
################ End function definitions ###############



################################################ Entry Point ############################################
# For testing only.  A message and a delay
print("Version 1.00 starting ...")
utime.sleep(1)

reset_cause = machine.reset_cause()
wakeup_reason = machine.wake_reason()
print("reset_cause: ", reset_cause)
print("wakeup_reason: ", wakeup_reason[0])

######################## Set the DS3231 alarm for the next interrupt ##############################
# Before any other action, set the next DS3231 alarm time and clear the DS3231 interrupt request.  If any of the functions hang,
#   the DS3231 will reset the GPy at the next alarm.
startup_datetime = set_next_alarm()

# Now that the DS3231 interrupt request is cleared, configure P22 as an interrupt pin to detect DS3231 interrupts.
ds3231_trigger = Pin('P22', mode=Pin.IN, pull=None)  # external pull up resistor on ds3231 reset pin
ds3231_trigger.callback(Pin.IRQ_FALLING, ds3231_int_handler)

# Configure P22 as a source for sleep wakeup when using machine.deepsleep()
pin_list = ['P22']
machine.pin_sleep_wakeup(pin_list, machine.WAKEUP_ALL_LOW, False)



######################## Read the battery voltage ############################## 
volts = battery_voltage()
print("Voltage = %5.2f V" % (volts))

# Use voltage as a string without a decimal so that it can be included in the photo filename
rounded_volts = 100 * round(volts,2)  # e.g., for volts = 6.475324, rounded_value = 648.
integer_volts = int(rounded_volts)    # for rounded_value = 648.  integer_value = 648 
string_volts = str(integer_volts)


# Read the DS3231 reset code from NVRAM
ds3231_reset = '0'
try:
    ds3231_reset = pycom.nvs_get("DS3231")
    print("DS3231 reset code: ", ds3231_reset)
except Exception as e:
    print("Failed to read DS3231_Interrupt from NVRAM: ", e)
    try:
        pycom.nvs_set("DS3231", 0)
    except Exception as e:
        print("Did not set the DS3231 key: ", e)

if ds3231_reset is None:
    ds3231_reset = 0
    try:
        pycom.nvs_set("DS3231", 0)
    except Exception as e:
        print("Did not set the DS3231 key: ", e)


#if reset_cause == 3 and wakeup_reason[0] != 2:
if ds3231_reset == 1:
    print("DS3231 interrupt")
    # Reset the DS3231 reset code
    try:
        pycom.nvs_set("DS3231", 0)
    except Exception as e:
        print("Did not set the DS3231 key: ", e)
    transmit_picture(volts)
else:
    print("The DS3231 did not interrupt, connecting to Pybytes")
    connect_to_pybytes(volts)

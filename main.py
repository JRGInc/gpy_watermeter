import machine
from machine import Pin         # To control the pin that RESETs the ESP32-CAM
from machine import UART        # Receiving pictures from the ESP32-CAM
from machine import RTC         # Software real time clock
from network import WLAN        # Connecting with the WiFi; Will not be needed when connecting with LTE
import base64                   # For encoding the picture
import urequests as requests    # Used for http transfer with the server
import utime                    # Time delays 


# Assign the Station ID number (0-99)
station_id = "55"


# For testing only.  A message and a delay
print("Starting ...")
utime.sleep(1)

while True:
    # Establish the WiFi connection as a station; External antenna
    wlan = WLAN(mode=WLAN.STA,antenna=WLAN.EXT_ANT,max_tx_pwr=70)  #range is 8 to 78

    wlan.connect(ssid='JRG Guest', auth=(WLAN.WPA2, '600guest'))
    #wlan.connect(ssid='JRG Bldg1', auth=(WLAN.WPA2, '7rg600ap!'))


    while not wlan.isconnected():
        utime.sleep(2)
        print("Wifi .. connecting")

    print("WiFi connected successfully")
    # Print the WiFi IP information
    print(wlan.ifconfig())

    rtc = machine.RTC()
    rtc.ntp_sync("pool.ntp.org")
    while not rtc.synced():
        machine.idle()
    #print("RTC synced with NTP time")


    # adjust for the local timezone. By default, NTP time will be GMT
    est_timezone = -5   # Eastern standard time is GMT - 5
    edt_timezone = -4   # Eastern daylight time is GMT - 4
    utime.timezone(edt_timezone*60**2)  # Calculate timezone using appropriate GMT offset

    #print("Local time after synchronization：%s" %str(utime.localtime()))
    #print("Local time as Epoch", utime.time())

    # Get current time and determine the time for the next picture.  This will be used to calculate the time to sleep (in seconds) after this picture is complete
    #      Get the local time as epoch and round down to the nearest minute
    rounded_time = utime.time()//60 * 60
  
    # Add the number of seconds to delay until the next picture.  In this case, take a picture every 2 minutes at 5 seconds past the minute (0:05, 2:05, 4:05, etc)
    next_time = rounded_time + 125
    #print("  epoch time for next picture", next_time)

    time_stamp = '{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}'.format(utime.localtime()[0], utime.localtime()[1], utime.localtime()[2], utime.localtime()[3], utime.localtime()[4], utime.localtime()[5])
    camera_time_stamp = '{:04d}{:02d}{:02d}{:02d}{:02d}'.format(utime.localtime()[0], utime.localtime()[1], utime.localtime()[2], utime.localtime()[3], utime.localtime()[4])

    # Picture filename.  Transmit this to the ESP32-CAM
    picture_filename = station_id + '_' + camera_time_stamp + '\0'
    #print("Timestamp", time_stamp)
    #print(picture_filename)  # Print the filename to make sure it is properly formatted

    # Define the trigger pin for waking up the ESP32-CAM
    #    When this pin is pulled LOW for approx 1ms
    #    and released to float HIGH, the ESP32-CAM
    #    will wake up, take a picture, save the picture
    #    to its on-board SD card and then transmit the
    #    picture over the UART to this Gpy
    # Make sure the trigger is floating HIGH
    #    For the OPEN_DRAIN mode, a value of 0 pulls
    #    the pin LOW and a value of 1 allows the
    #    pin to float at the pull-up level
    #    The ESP32-CAM RESET pin is internally pulled up.
    camera_trigger = Pin('P8', mode=Pin.OPEN_DRAIN, value=1)


    # Define uart for UART1.  This is the UART that
    #    receives data from the ESP32-CAM
    #    For now, the ESP32-CAM transmits to the GPy at 38400 bps.  This can probably be increased.
    uart = UART(1, baudrate=38400)




    # Toggle the ESP32-CAM RESET line to initiate the picture capture process
    camera_trigger(0)
    utime.sleep_ms(10)
    camera_trigger(1)

    # wait for esp32-cam to send the boot up data
    #   The delay value is empirical and depends on the
    #   delay values hard coded in the ESP32-CAM code as
    #   well as the time required for the ESP32-CAM to initialize.
    #time.sleep_ms(2000)

    # For testing only.  Print a string to the GPy terminal
    print('new picture')

    # Skip through all of the ESP32-CAM startup diagnostic data that is
    #    transmitted.  Eventually, modify the ESP32-CAM driver
    #    firmware to eliminate this diagnostic information.
    #count = uart.any()
    #data_bytes = uart.read(count)


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
    #print(picture_filename)
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

    # Array for receiving picture bytes (unsigned char) from the ESP32-CAM
    buf = bytearray(picture_len_int)
    mv = memoryview(buf)
    idx = 0

    print('Begin transfer')
    # Receive the picture data and store in the buffer
    while idx < len(buf):
        if uart.any():
            bytes_read = uart.readinto(mv[idx:])
            idx += bytes_read
            print('.', end='')

    # Print the index counter.  This is the number of bytes copied to the picture buffer
    print(idx)

    # Turn off the UART port
    uart.deinit()

    # Ecode the image using base64
    # The image is already and array of bytes so encode the array
    b64_picture_bytes = base64.b64encode(buf)

    # Convert the base64-encoded bytes to a string
    b64_picture_str = b64_picture_bytes.decode('ascii')

    # Print the encoded string length
    encoded_picture_len = len(b64_picture_str)
    print(encoded_picture_len)

    # Transmit the encoded image to the server
    url = "http://gaepd.janusresearch.com:8555/file/base64"


    data_file = "{\"base64File\": \"" +  b64_picture_str + "\", \"id\": " + station_id + ", \"timeStamp\": \"" + time_stamp + "\"}"

    headers = {
        'Content-Type': 'application/json',
    }

    try:
        response = requests.post(url, headers=headers, data=data_file)
        print(response.text)  # Prints the return filename from the server in json format
    except Exception as e:
        print(e)


    # For testing only.  Indicates that the picture processing (capture, encode, transmit) is complete
    print('end picture')

    # Picture transfer is complete so disconnect from the WLAN
    wlan.disconnect()

    # The number of seconds to delay is the previously calculated "next_time" minus the current time
    delay_seconds = next_time - utime.time()

    # make sure delay_seconds is a positive number with enough time to respond
    if delay_seconds < 5:
        delay_seconds = 15

    #print("Delay (seconds): ", delay_seconds)
    # Adjust delay time.  In this case (for example) take a picture at 5 seconds after every minute
    # Delay before taking the next picture
    utime.sleep(delay_seconds)
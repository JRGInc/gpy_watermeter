import machine                  # for machine.idle when using wlan

# reset_cause:
#         0 Pressing the reset button on the GPy
#         1
#         2 WDT Reset (Uploading the code causes this, also)

#print("Reset code: ", machine.reset_cause())
#print("Wake reason: ", machine.wake_reason())
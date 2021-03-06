#!/usr/bin/env python
import threading
import time
import logging
import argparse

from meter import read_meter, Meter_Exception
#from meter_photo import read_meter, Meter_Exception
from wristband import wristband, WB_Exception
import diff
from xively import xively, Xively_Exception


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description="read meter, post to internet and send to energy wristband")
    
    from ConfigParser import ConfigParser, NoSectionError
    config = ConfigParser()
    config.read('config.rc')
    d_ble_address = config.get('ble', 'address')
    d_ble_timeout = config.get('ble', 'timeout')
    d_max_energy = config.get('energy', 'max_energy')
    d_max_time = config.get('energy', 'max_time')
    d_sens = config.get('energy', 'sensitivity')
    d_udp_repeat = config.get('daemon', 'udp_repeat')

    parser.add_argument('--udp_repeat', action='store_const', const=True,
        default=d_udp_repeat,
        help="increase coverage by broadcasting via UDP to other computers")
    parser.add_argument('--max_energy', action='store', type=int,
        help="max energy", default=d_max_energy)
    parser.add_argument('--max_time', action='store', type=int,
        help="max time before disregarding energy", default=d_max_time)
    parser.add_argument('--sens', action='store', type=int,
        help="sensitivity of differentiation in W/s", default=d_sens)
    parser.add_argument('-d','--debug',
        help='print lots of debugging statements',
        action="store_const", dest="loglevel", const=logging.DEBUG,
        default=logging.WARNING)
    parser.add_argument('-v','--verbose',
        help='be verbose',
        action="store_const", dest="loglevel", const=logging.INFO)
    parser.add_argument('--meter_port', help="current cost meter port",
        default="/dev/ttyUSB0")
    parser.add_argument('--meter_timeout', type=int, help="meter timeout",
        default=10)
    parser.add_argument('--ble_address', help="BLE address of wristband",
        default = d_ble_address)
    parser.add_argument('--wristband_timeout', action='store', type=int, 
        help="timeout for gatttool", default=d_ble_timeout)
    parser.add_argument('--xively_feed', help="id of your xively feed",
        default = None)

    args = parser.parse_args()

    xively_timeout = 10

    # wrist band
    data_interval = 60 * 10  # seconds
    wb = wristband(logging, args.ble_address, 
            args.wristband_timeout,
            udp_repeat=args.udp_repeat)

    # get diff object
    diff = diff.diff_energy(logging, max_energy=args.max_energy,
        sens=args.sens,
        max_time=args.max_time)    

    # set this in the past so wristband is updated when daemon starts
    last_data = time.time() - data_interval

    # set up logging to file
    log_format = '%(asctime)s %(name)-10s %(levelname)-8s %(message)s'
    logging.basicConfig(level=args.loglevel,
                        format=log_format,
                        filename='daemon.log')

    # startup messages
    logging.warning("daemon started")
    logging.warning("max energy=%dW, sens=%dW/s" % (args.max_energy,args.sens))
    logging.warning("BLE address=%s timeout=%d" % (args.ble_address, args.wristband_timeout))
    if args.xively_feed is not None:
        logging.warning("xively feed id=%s" % args.xively_feed)

    # main loop
    while True:
        try:
            # read meter, might raise an exception
            (temp, energy) = read_meter(args.meter_port, logging, args.meter_timeout)
            time.sleep(5)
            logging.info("meter returned %dW %.1fC" % (energy, temp))

            # update internet service - run as a daemon thread
            if args.xively_feed is not None:
                xively_t = xively(args.xively_feed, logging, timeout=xively_timeout, uptime=True)
                xively_t.daemon = True  # could this be done in the class?
                xively_t.add_datapoint('temperature', temp)
                xively_t.add_datapoint('energy', energy)

            # get last good energy point
            last_energy = diff.get_last_valid(energy)

            # convert the real energies to divisions from 1 to 4
            energy_div = diff.energy_to_div(energy)
            last_energy_div = diff.energy_to_div(last_energy)

            # send/receive to the wristband? can raise exceptions
            try:
                # need to send?
                if energy_div != last_energy_div:
                    if args.xively_feed is not None:
                        xively_t.add_datapoint('wb-this', energy_div)
                    # this blocks but times out
                    wb.send(last_energy_div, energy_div)

                # need to fetch/update wristband?
                if time.time() > last_data + data_interval:
                    last_data = time.time()

                    # resend last energy value in case previous send failed
                    logging.info("resending last energy %d" % energy_div)
                    wb.re_send(energy_div)

                    # fetch data
                    (battery, uptime) = wb.get()
                    if args.xively_feed is not None:
                        xively_t.add_datapoint('wb-battery', battery)
                        xively_t.add_datapoint('wb-uptime', uptime)

            except WB_Exception as e:
                logging.warning(e)

            if args.xively_feed is not None:
                logging.info("send data to xively")
                xively_t.start()
        except Meter_Exception as e:
            logging.info(e)
            # prevent rapid looping
            time.sleep(1)
        except KeyboardInterrupt as e:
            logging.warning("caught interrupt - quitting")
            break
        except Xively_Exception as e:
            logging.error(e)
            break 
        except Exception as e: # catch all
            logging.error("unexpected error!")
            logging.error(e)
            break
        # keep a track of running threads
        logging.debug("%d threads running", len(threading.enumerate()))

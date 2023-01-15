#!/usr/bin/env python
# Ecowitt Direct - Ecowitt Local Personal Weather Station Conditions
# -*- coding: utf-8 -*-
"""
 Python module to poll and store weather data from Ecowitt Direct for Hyper-Local Weather

 Author: BJReplay
 Based On Weather411 module by: Jason A. Cox
 For more information see https://github.com/jasonacox/Powerwall-Dashboard

 Weather Data Tool
    This tool will poll current weather conditions using Ecowitt Direct Access to local (LAN) Weather Station
    and then make it available via local API calls or optionally store 
    it in InfluxDB.

    Ecowitt Direct Access information: https://www.ecowitt.com/shop/forum/forumDetails/496

    CONFIGURATION FILE - On startup will look for ecowittdirect.conf
    which includes the following parameters:

        [LocalWeather]
        DEBUG = no

        [API]
        # Port to listen on for requests (default 8686)
        ENABLE = yes
        PORT = 8696 # Different Port to Weather 411 and ecowitt so they can co-exist

        [EcowittDirect]
        # Set your IP Address to the IP Address of your Ecowitt device - best to set to a static least in your router
        IP = 192.168.0.2
        
        # Wait in 10-Second Increments (not minutes as per other weather services for Powerwall-Dashboard)
        WAIT = 1
        TIMEOUT = 10
        
        [InfluxDB]
        # Record data in InfluxDB server 
        ENABLE = yes
        HOST = influxdb
        PORT = 8086
        DB = powerwall
        FIELD = ecowitt

    ENVIRONMENTAL:
        LOCALWEATHERCONF = "Path to localweather.conf file"

    The API service of LocalWeather has the following functions:
        /           - Human friendly display of current weather conditions
        /json       - All current weather data in JSON format
        /temp       - Current temperature in C
        /humidity   - Current humidity in %
        /pressure   - Current pressure in hPa
        /solar      - Current Insolation in W/m²
        /uvi        - Current UV Index (scale 1 to 11)
        /wind       - Current speed (km/h), gust (km/h) and direction (degree)
        /rain       - Precipitation volume in mm (last hour / daily)
        /aqi        - Air Quality measurements
        /indoor     - Indoor temperature and pressure measurements
        /time       - Current time in UTC


"""
# Modules
from __future__ import print_function
import threading
import time
import logging
import json
import requests
import resource
import datetime
import sys
import os
import re
import math
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from socketserver import ThreadingMixIn 
import configparser
from influxdb import InfluxDBClient

BUILD = "0.0.1"
CLI = False
LOADED = False
CONFIG_LOADED = False
CONFIGFILE = os.getenv("WEATHERCONF", "ecowittdirect.conf")

# Load Configuration File
config = configparser.ConfigParser(allow_no_value=True)
if os.path.exists(CONFIGFILE):
    config.read(CONFIGFILE)
    DEBUGMODE = config["LocalWeather"]["DEBUG"].lower() == "yes"

    # LocalWeather API
    API = config["API"]["ENABLE"].lower() == "yes"
    APIPORT = int(config["API"]["PORT"])

    # Ecowitt
    ECOWAIT = int(config["EcowittDirect"]["WAIT"])
    ECOIP = str(config["EcowittDirect"]["IP"])
    TIMEOUT = str(config["EcowittDirect"]["TIMEOUT"])

    # InfluxDB
    INFLUX = config["InfluxDB"]["ENABLE"].lower() == "yes"
    IHOST = config["InfluxDB"]["HOST"]
    IPORT = int(config["InfluxDB"]["PORT"])
    IUSER = config["InfluxDB"]["USERNAME"]
    IPASS = config["InfluxDB"]["PASSWORD"]
    IDB = config["InfluxDB"]["DB"]
    IFIELD = config["InfluxDB"]["FIELD"]

else:
    # No config file - Display Error
    sys.stderr.write("LocalWeather Server %s\nERROR: No config file. Fix and restart.\n" % BUILD)
    sys.stderr.flush()
    while(True):
        time.sleep(3600)

URL = "http://" + ECOIP + "/get_livedata_info"

# Logging
log = logging.getLogger(__name__)
if DEBUGMODE:
    logging.basicConfig(format='%(levelname)s:%(message)s',level=logging.DEBUG)
    log.setLevel(logging.DEBUG)
    log.debug("LocalWeather [%s]\n" % BUILD)

# Global Stats
serverstats = {}
serverstats['LocalWeather'] = BUILD
serverstats['gets'] = 0
serverstats['errors'] = 0
serverstats['timeout'] = 0
serverstats['uri'] = {}
serverstats['ts'] = int(time.time())         # Timestamp for Now
serverstats['start'] = int(time.time())      # Timestamp for Start 
serverstats['clear'] = int(time.time())      # Timestamp of lLast Stats Clear
serverstats['influxdb'] = 0
serverstats['influxdberrors'] = 0

# Global Variables
running = True
weather = {}
raw = {}

OBSERVATION_MAP = {
    "0x02": "temperature",
    "0x07": "humidity",
    "0x03": "dewpoint",
    "0x04": "windchill",
    "0x05": "heatindex",
    "0x0A": "wind_deg",
    "0x0B": "wind_speed",
    "0x0C": "wind_gust",
    "0x0E": "rain_1h",
    "0x10": "rain_24h",
    "0x15": "solar",
    "0x16": "uvi",
    "0x17": "uvi",
}

WH25_MAP = {
    "intemp": "inside_temp",
    "inhumi": "inside_humidity",
    "abs": "pressure",
}

CO2_MAP = {
    "PM25": "pm25",
    "PM25_RealAQI": "pm25aqi",
    "PM10": "pm10",
    "PM10_RealAQI": "pm10aqi",
    "CO2": "co2",
}

# Helper Functions
def clearweather():
    global weather
    weather = {
        # header
        "dt": 0, 
        # basics
        "temperature": None, "humidity": None, "pressure": None, 
        "feels_like": None, "app_temp": None, 
        # wind
        "wind_speed": None, "wind_deg": None, "wind_gust": None,
        # precipitation
        "rain_1h": 0.0, "rain_24h": 0.0,
        # solar_and_uvi
        "solar": 0.0, "uvi": 0, 
        # indoor
        "inside_temp": None, "inside_humidity": None, 
        # AQI
        "pm25": None, "pm25aqi": None, "pm10": None, "pm10aqi": None, "co2": None,
        }

def getval(val):
    # strip and return value
    return round(re.sub(r'[^\d.]+', '', val), 1)

def app_temp(temp, humidity, wind):
    # Calculate Steadman Apparent Temperature as per BOM, ignoring Radation
    # http://www.bom.gov.au/info/thermal_stress/?cid=003bl08
    wind_float = float(wind)
    temp_float = float(temp)
    humi_int = int(humidity)
    wind_ms = wind_float / 3.6
    e = humi_int / 100 * 6.105 * math.exp(17.27 * temp_float / ( 237.7 + temp_float ))
    return temp_float + (0.33 * e) - (0.70 * wind_ms) - 4.00

# Clear weather data
clearweather()

# Threads
def fetchWeather():
    """
    Thread to poll for current weather conditions
    """
    global running, weather, LOADED, raw, serverstats, URL
    sys.stderr.write(" + fetchWeather thread\n")
    nextupdate = time.time()

    # Time Loop to update current weather data
    while(running):
        currentts = time.time()
        lasttime = time.time()
        # Is it time for an update?
        if currentts >= nextupdate:
            nextupdate = currentts + (10 * ECOWAIT)
            if CLI:
                print("\n")
            try:
                response = requests.get(URL)
                if response.status_code == 200:
                    raw = response.json()
                    clearweather()
                    try:
                        weather["dt"] = int(lasttime)
                        if "common_list" in raw:
                            for observation in raw["common_list"]:
                                if observation["id"] in OBSERVATION_MAP:
                                    weather[OBSERVATION_MAP[(observation["id"])]] = getval(observation["val"])
                        if "rain" in raw:
                            for observation in raw["rain"]:
                                if observation["id"] in OBSERVATION_MAP:
                                    weather[OBSERVATION_MAP.get(observation["id"])] = getval(observation["val"])
                        if "wh25" in raw:
                            for wh25_values in raw["wh25"]:
                                for wh25_id, wh25_value in wh25_values.items():
                                    if wh25_id in WH25_MAP:
                                        weather[WH25_MAP.get(wh25_id)] = getval(wh25_value)
                        if "co2" in raw:
                            for co2_values in raw["co2"]:
                                for co2_id, co2_value in co2_values.items():
                                    if co2_id in CO2_MAP:
                                        weather[CO2_MAP.get(co2_id)] = getval(co2_value)
                        weather["app_temp"] = app_temp(weather["temperature"], weather["humidity"], weather["wind_speed"])
                    except:
                        log.debug("Data error in payload from Ecowitt")
                        pass

                    log.debug("Weather data loaded")
                    LOADED = True

                    if INFLUX:
                        log.debug("Writing to InfluxDB")
                        try:
                            client = InfluxDBClient(host=IHOST,
                                port=IPORT,
                                username=IUSER,
                                password=IPASS,
                                database=IDB)
                            output = [{}]
                            output[0]["measurement"] = IFIELD
                            output[0]["time"] = int(currentts)
                            output[0]["fields"] = {}
                            for i in weather:
                                output[0]["fields"][i] = weather[i]
                            log.debug(output)
                            # print(output)
                            if client.write_points(output, time_precision='s'):
                                serverstats['influxdb'] += 1
                            else:
                                serverstats['influxdberrors'] += 1
                            client.close()
                        except:
                            log.debug("Error writing to InfluxDB")
                            sys.stderr.write("! Error writing to InfluxDB\n")
                            serverstats['influxdberrors'] += 1
                            pass
                else:
                    # showing the error message
                    log.debug("Bad response from Ecowitt")
                    sys.stderr.write("! Bad response from Ecowitt\n")
            except:
                log.debug("Error fetching Ecowitt")
                sys.stderr.write("! Error fetching Ecowitt\n")
                pass
        time.sleep(5)
    sys.stderr.write('\r ! fetchWeather Exit\n')

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    pass

class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        if DEBUGMODE:
            sys.stderr.write("%s - - [%s] %s\n" %
                         (self.address_string(),
                          self.log_date_time_string(),
                          format%args))
        else:
            pass

    def address_string(self):
        # replace function to avoid lookup delays
        host, hostport = self.client_address[:2]
        return host

    def do_GET(self):
        global weather, LOADED, URL
        self.send_response(200)
        message = "Error"
        contenttype = 'application/json'
        result = {}  # placeholder
        if self.path == '/':
            # Display friendly intro
            contenttype = 'text/html'
            message = '<html>\n<head><meta http-equiv="refresh" content="5" />\n'
            message += '<style>p, td, th { font-family: Helvetica, Arial, sans-serif; font-size: 10px;}</style>\n' 
            message += '<style>h1 { font-family: Helvetica, Arial, sans-serif; font-size: 20px;}</style>\n' 
            message += '</head>\n<body>\n<h1>LocalWeather Server v%s</h1>\n\n' % BUILD
            if not LOADED:
                message = message + "<p>Error: No weather data available</p>"
            else:
                message = message + '<table>\n<tr><th align ="right">Current</th><th align ="right">Value</th></tr>'
                for i in weather:
                    message = message + '<tr><td align ="right">%s</td><td align ="right">%s</td></tr>\n' % (i, weather[i])
                message = message + "</table>\n"
            message = message + '<p>Last data update: %s<br><font size=-2>From URL: %s</font></p>' % (
                str(datetime.datetime.fromtimestamp(int(weather['dt']))), URL)
            message = message + '\n<p>Page refresh: %s</p>\n</body>\n</html>' % (
                str(datetime.datetime.fromtimestamp(time.time())))
        elif self.path == '/stats':
            # Give Internal Stats
            serverstats['ts'] = int(time.time())
            serverstats['mem'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            message = json.dumps(serverstats)
        elif self.path == '/json' or self.path == '/all':
            message = json.dumps(weather)
        elif self.path == '/raw':
            message = json.dumps(raw)
        elif self.path == '/time':
            ts = time.time()
            result["local_time"] = str(datetime.datetime.fromtimestamp(ts))
            result["ts"] = ts
            result["utc"] = str(datetime.datetime.utcfromtimestamp(ts)) 
            message = json.dumps(result)
        elif self.path == '/temp':
            result["temperature"] = weather["temperature"]
            message = json.dumps(result)            
        elif self.path in ["/temperature","/humidity","/pressure","/feels_like","/app_temp"]:
            i = self.path.split("/")[1]
            result[i] = weather[i]
            message = json.dumps(result)
        elif self.path == '/wind':
            result["wind_speed"] = weather['wind_speed']
            result["wind_deg"] = weather['wind_deg']
            result["wind_gust"] = weather['wind_gust']
            message = json.dumps(result)
        elif self.path == '/solar':
            result["solar"] = weather['solar']
            message = json.dumps(result)
        elif self.path == '/uvi':
            result["uvi"] = weather['uvi']
            message = json.dumps(result)
        elif self.path == '/indoor':
            result["inside_temp"] = weather["inside_temp"]
            result["inside_humidity"] = weather["inside_humidity"]
            message = json.dumps(result)            
        elif self.path == '/aqi':
            result["pm25"] = weather['pm25']
            result["pm25aqi"] = weather['pm25aqi']
            result["pm10"] = weather['pm10']
            result["pm10aqi"] = weather['pm10aqi']            
            result["co2"] = weather['co2']
            message = json.dumps(result)            
        elif self.path in ['/rain', '/precipitation']:
            result["rain_1h"] = weather['rain_1h']
            result["rain_3h"] = weather['rain_3h']
            result["snow_1h"] = weather['snow_1h']
            result["snow_3h"] = weather['snow_3h']            
            result["rain_24h"] = weather['rain_24h']
            message = json.dumps(result)
        elif self.path == '/conditions' or self.path == '/weather':
            result["conditions"] = weather['weather_main']
            result["weather_description"] = weather['weather_description']
            result["weather_icon"] = weather['weather_icon']
            message = json.dumps(result)
        else:
            # Error
            message = "Error: Unsupported Request"

        # Counts 
        if "Error" in message:
            serverstats['errors'] = serverstats['errors'] + 1
        else:
            if self.path in serverstats["uri"]:
                serverstats["uri"][self.path] += 1
            else:
                serverstats["uri"][self.path] = 1
        serverstats['gets'] = serverstats['gets'] + 1

        # Send headers and payload
        self.send_header('Content-type',contenttype)
        self.send_header('Content-Length', str(len(message)))
        self.end_headers()
        self.wfile.write(bytes(message, "utf8"))

def api(port):
    """
    API Server - Thread to listen for commands on port 
    """
    sys.stderr.write(" + apiServer thread - Listening on http://localhost:%d\n" % port)

    with ThreadingHTTPServer(('', port), handler) as server:
        try:
            # server.serve_forever()
            while running:
                server.handle_request()
        except:
            print(' CANCEL \n')
    sys.stderr.write('\r ! apiServer Exit\n')

# MAIN Thread
if __name__ == "__main__":
    # Create threads
    thread_fetchWeather = threading.Thread(target=fetchWeather)
    thread_api = threading.Thread(target=api, args=(APIPORT,))
    
    # Print header
    sys.stderr.write("LocalWeather Server [%s]\n" % (BUILD))
    sys.stderr.write("* Configuration Loaded [%s]\n" % CONFIGFILE)
    sys.stderr.write(" + LocalWeather - Debug: %s, Activate API: %s, API Port: %s\n" 
        % (DEBUGMODE, API, APIPORT))
    sys.stderr.write(" + Ecowitt - Wait: %s, Timeout: %s\n"
        % (ECOWAIT, TIMEOUT))
    sys.stderr.write(" + InfluxDB - Enable: %s, Host: %s, Port: %s, DB: %s, Field: %s\n"
        % (INFLUX, IHOST, IPORT, IDB, IFIELD))
    
    # Start threads
    sys.stderr.write("* Starting threads\n")
    thread_fetchWeather.start()
    thread_api.start()
    sys.stderr.flush()
    
    if CLI:
        print("   %15s | %4s | %8s | %8s | %5s | %10s" %
            ('timezone','Temp','Humidity','Pressure','Cloud','Visibility') )
    try:
        while(True):
            if CLI and 'name' in weather and weather['name'] is not None:
                # weather report
                print("   %15s | %4d | %8d | %8d | %5d | %10d" %
                    (weather['name'], weather['temperature'], 
                    weather['humidity'], weather['pressure'], 
                    weather['cloudiness'], weather['visibility']),
                    end='\r')
            time.sleep(2)
    except (KeyboardInterrupt, SystemExit):
        running = False
        # Close down API thread
        requests.get('http://localhost:%d/stop' % APIPORT)
        print("\r", end="")

    sys.stderr.write("* Stopping\n")
    sys.stderr.flush()

# Ecowitt Local Weather Server

![Docker Pulls](https://img.shields.io/docker/pulls/jasonacox/ecowittdirect)

This server pulls current weather data from a local [Ecowitt device live data feed](https://www.ecowitt.com/shop/forum/forumDetails/496), makes it available via local API calls and stores the data in InfluxDB for graphing.

This service was built to easily add weather data graphs to the [Powerwall-Dashboard](https://github.com/jasonacox/Powerwall-Dashboard) project.

Docker: docker pull [jasonacox/ecowitt](https://hub.docker.com/r/jasonacox/ecowittdirect)

## Quick Start


1. Create a `ecowittdirect.conf` file in /weather/contrib/ecowittdirect (`cp ecowittdirect.conf.sample ecowittdirect.conf`) and update with your specific device IP address:

    * Enter your Device IP Address.

    ```python
    [LocalWeather]
    DEBUG = no

    [API]
    # Port to listen on for requests (default 8696)
    ENABLE = yes
    PORT = 8696 # Different Port to Weather 411 and ecowitt so they can co-exist

    [ecowittdirect]
    # Set your IP Address to the IP Address of your Ecowitt device - best to set to a static least in your router
    IP = 192.168.0.2
    
    # Wait in 10-Second increments (not minutes as per other weather services for Powerwall-Dashboard)
    WAIT = 1
    TIMEOUT = 10

    [InfluxDB]
    # Record data in InfluxDB server 
    ENABLE = yes
    HOST = influxdb
    PORT = 8086
    DB = powerwall
    FIELD = localweather
    # Leave blank if not used
    USERNAME = 
    PASSWORD =

2. Run the Docker Container to listen on port 8686.

    ```bash
    docker run \
    -d \
    -p 8696:8696 \
    -e WEATHERCONF='/var/lib/weather/ecowittdirect.conf' \
    -v ${PWD}:/var/lib/weather \
    --name ecowittdirect \
    --restart unless-stopped \
    jasonacox/ecowittdirect
    ```

3. Test the API Service

    Website of Current Weather: http://localhost:8686/

    ```bash
    # Get Current Weather Data
    curl -i http://localhost:8696/temp
    curl -i http://localhost:8696/all
    curl -i http://localhost:8696/conditions

    # Get Proxy Stats
    curl -i http://localhost:8696/stats

    # Clear Proxy Stats
    curl -i http://localhost:8696/stats/clear
    ```

4. Incorporate into Powerwall-Dashboard

    Add into powerwall.yml in your Powerwall-Dashboard folder

    ```yaml
    ecowitt:
        # Uncomment next line to build locally
        # build: ./weather/contrib/ecowittdirect
        image: jasonacox/ecowittdirect:latest
        container_name: ecowittdirect
        hostname: ecowittdirect
        restart: always
        user: "1000:1000"
        volumes:
            - type: bind
              source: ./weather/contrib/ecowittdirect
              target: /var/lib/ecowittdirect
              read_only: true
        ports:
            - target: 8696
              published: 8696
              mode: host
        environment:
            - WEATHERCONF=/var/lib/ecowittdirect/ecowittdirect.conf
        depends_on:
            - influxdb
    ```

    Optionally remove the weather411 section if you're not going to be running both Weather 411 and Local Weather

    ```bash
    ./compose-dash.sh stop
    ./compose-dash.sh up -d
    ```


## Build Your Own

This folder contains the `server.py` script that runs a multi-threaded python based API webserver.  

The `Dockerfile` here will allow you to containerize the proxy server for clean installation and running.

1. Build the Docker Container

    ```bash
    # Build for local architecture  
    docker build -t jasonacox/ecowittdirect:latest .

    # Build for all architectures - requires Docker experimental 
    docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 -t jasonacox/ecowittdirect:latest . 

    ```

2. Setup the Docker Container to listen on port 8686.

    ```bash
    docker run \
    -d \
    -p 8696:8696 \
    -e WEATHERCONF='/var/lib/weather/ecowittdirect.conf' \
    --name ecowittdirect \
    -v ${PWD}:/var/lib/weather \
    --restart unless-stopped \
    jasonacox/ecowittdirect
    ```

3. Test the API

    ```bash
    curl -i http://localhost:8696/temp
    curl -i http://localhost:8696/stats
    ```

    Browse to http://localhost:8696/ to see current weather conditions.


## Troubleshooting Help

If you see python errors, make sure you entered your credentials correctly in `docker run`.

```bash
# See the logs
docker logs ecowittdirect

# Stop the server
docker stop ecowittdirect

# Start the server
docker start ecowittdirect
```

## Release Notes

### 0.0.1 - Initial Build

* Initial Release

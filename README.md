# united_weather_ext extension

## Building and signing

* `dt-sdk build .`

## Running

* `dt-sdk run`

## Developing

1. Clone this repository
2. Install dependencies with `pip install .`
3. Increase the version under `extension/extension.yaml` after modifications
4. Run `dt-sdk build`

## Structure

### united_weather_ext folder

Contains the python code for the extension

### extension folder

Contains the yaml and activation definitions for the framework v2 extension

### setup.py

Contains dependency and other python metadata

### activation.json

Used during simulation only, contains the activation definition for the extension

# United Weather Extension

This extension pulls weather data into a Dynatrace environment via the v2 Custom Metrics and Custom Events API endpoints. By specifying **latitude**, **longitude**, and an **airport code** for each location, you can monitor real-time, precise location-based weather conditions. The airport code is just a convenient identifier (it is not used for querying the weather.gov API).

## How it Works

- The extension queries the [weather.gov API](https://www.weather.gov/documentation/services-web-api) for:
  - **Daily and Hourly Forecast**  
  - **Active Weather Alerts**  
  - **Latest Observations** from nearby stations (if available)

- Alerts are sent to Dynatrace as **Custom Events** (e.g., `Weather Alert - Tornado Warning - ORD`).
- Forecast and observation data are ingested as **Custom Metrics** under the names you see below.

## Metrics

Below is a short list of example metrics emitted by the extension. For each metric line, dimensions such as `airport_code`, `forecast_period`, etc., are attached:

- `weather.precipitationProbability`
- `weather.windSpeed`
- `weather.temperature.celsius`
- `weather.temperature.fahrenheit`
- Additional metrics for hourly forecasts (`weather.hourly.*`) and observed data (`weather.observed.*`) are also reported.

## Configuration

You will be prompted for:

1. **Dynatrace Token**  
   A Dynatrace API token with `metrics:ingest` permission.

2. **Dynatrace Base URL**  
   Typically your tenant URL, e.g. `https://<your-tenant>.live.dynatrace.com`.

3. **Locations (JSON)**  
   A JSON object mapping an airport code or location code to latitude/longitude. Example:

    ```json
    {
      "ORD": { "name": "Chicago OHare", "lat": "41.8781", "lon": "-87.6298" },
      "IAH": { "name": "Houston",      "lat": "29.9844", "lon": "-95.3414" },
      "EWR": { "name": "Newark",       "lat": "40.6895", "lon": "-74.1745" },
      "DEN": { "name": "Denver",       "lat": "39.8561", "lon": "-104.6737" },
      "SFO": { "name": "San Francisco","lat": "37.6213", "lon": "-122.3790" }
    }
    ```

4. **Temperature Unit**  
   `1` = Fahrenheit, `2` = Celsius, `3` = Both

5. **Polling Frequency (seconds)**  
   How often to call the weather.gov APIs.

6. **Thread Count**  
   How many concurrent calls to weather.gov to allow (used for asynchronous calls).

## License and Author

- This extension uses the public [weather.gov API](https://www.weather.gov/documentation/services-web-api) which requires no authentication.
- Author: Aaron Jacobs / AHEAD Consulting  


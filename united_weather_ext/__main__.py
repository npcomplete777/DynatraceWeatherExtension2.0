import json
import re
import requests
import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from dynatrace_extension import Extension, DtEventType

print("Loading WeatherExtension...")

class WeatherApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Weather API Error: {status_code} - {message}")

class ExtensionImpl(Extension):
    def initialize(self) -> None:
        """
        Called once at startup. Reads activation config (including secrets),
        extracts config from the first endpoint, and schedules the query() callback.
        """
        try:
            self.logger.info(f"Weather Extension Version: {self.get_version()}")
            self.logger.info("initialize() has started.")

            config = self.get_activation_config()
            merged_config = config.config if hasattr(config, "config") else config
            self.logger.info(f"Activation config keys: {list(merged_config.keys())}")

            endpoint_config = self._get_endpoint_config(merged_config)
            self._configure_from_endpoint(endpoint_config)

            # Schedule the main query
            self.schedule(self.query, timedelta(seconds=self.polling_frequency))
            self.logger.info("initialize() completed successfully.")
        except Exception as e:
            self.logger.error(f"Exception in initialize(): {repr(e)}")
            raise

    def _get_endpoint_config(self, merged_config: Dict) -> Dict:
        """
        Looks for 'endpoints' in multiple known config paths and returns the first found.
        """
        for section in ['endpoints', 'pythonLocal.endpoints', 'pythonRemote.endpoints']:
            path = section.split('.')
            config = merged_config
            for key in path:
                config = config.get(key, {})
            if config and isinstance(config, list) and len(config) > 0:
                return config[0]
        raise Exception("No endpoints found in activation config.")

    def _configure_from_endpoint(self, endpoint_config: Dict) -> None:
        """
        Extracts basic parameters from the endpoint config (token, base URL, hubs, etc.).
        """
        self.dynatrace_token = endpoint_config.get("dynatraceToken")
        self.dynatrace_base_url = endpoint_config.get("dynatraceBaseUrl")
        self.temperature_unit = int(endpoint_config.get("temperatureUnit", "3"))
        self.polling_frequency = int(endpoint_config.get("pollingFrequency", "60"))
        self.thread_count = int(endpoint_config.get("threadCount", "8"))

        hubs_str = endpoint_config.get("unitedHubs", "{}")
        try:
            self.united_hubs = json.loads(hubs_str)
        except Exception as e:
            self.logger.error(f"Error parsing unitedHubs: {e}")
            self.united_hubs = {}

    def on_shutdown(self) -> None:
        self.logger.info("Weather Extension shutting down...")

    def query(self) -> None:
        """
        Main callback executed periodically.
        1) Reports a heartbeat metric
        2) Fetches and reports any active alerts as events
        3) Asynchronously processes forecast/observations as metrics
        """
        self.logger.info("query() started for weather extension.")
        self.report_metric("weather.extension.heartbeat", 1)
        self.logger.info("Heartbeat metric reported.")

        # Synchronous weather alerts
        for airport_code, hub_info in self.united_hubs.items():
            try:
                name = hub_info.get("name", airport_code)
                lat = hub_info.get("lat")
                lon = hub_info.get("lon")
                url = f"https://api.weather.gov/alerts/active?status=actual&point={lat}%2C{lon}"

                self.logger.info(f"Fetching alerts for {name} ({airport_code}) from {url}")
                resp = requests.get(url, headers={
                    "User-Agent": "United Airlines Weather Extension",
                    "Accept": "application/geo+json"
                })
                resp.raise_for_status()

                data = resp.json()
                features = data.get("features", [])
                if features:
                    self.logger.info(f"Found {len(features)} alerts for {airport_code}.")
                    for feature in features:
                        evt = self.create_event(feature, airport_code, name)
                        properties = self.enrich_alert_properties(feature.get("properties", {}))
                        properties.update(evt["properties"])

                        # Report each alert as a Dynatrace event
                        self.report_dt_event(
                            event_type=DtEventType.CUSTOM_INFO,
                            title=evt["title"],
                            start_time=evt["start_time_ms"],
                            end_time=evt["end_time_ms"],
                            properties=properties
                        )
                else:
                    self.logger.info(f"No alerts found for {airport_code}.")
            except Exception as e:
                self.logger.error(f"Error in query() for {airport_code}: {e}")

        # Asynchronous forecast processing
        try:
            asyncio.run(self.async_query_metrics())
        except Exception as e:
            self.logger.error(f"Error in async_query_metrics: {e}")

        self.logger.info("query() ended successfully.")

    def enrich_alert_properties(self, alert: Dict[str, Any]) -> Dict[str, str]:
        """
        Extracts and returns extra fields from weather.gov alerts to add to the event properties.
        """
        return {
            "severity": str(alert.get("severity")),
            "certainty": str(alert.get("certainty")),
            "urgency": str(alert.get("urgency")),
            "event_type": str(alert.get("event")),
            "response_type": str(alert.get("response")),
            "affected_zones": str(alert.get("affectedZones", [])),
            "instruction": str(alert.get("instruction")),
            "sender": str(alert.get("senderName"))
        }

    def create_event(self, feature: Dict[str, Any], airport_code: str, airport_name: str) -> Dict[str, Any]:
        """
        Constructs a custom event dictionary from the alert JSON.
        """
        properties = feature.get("properties", {})
        onset = properties.get("onset")
        ends = properties.get("ends")
        start_ms = self.to_millis(onset) or int(datetime.now().timestamp() * 1000)
        end_ms = self.to_millis(ends) or (start_ms + 3600000)  # Default 1 hour if not provided

        evt = {
            "title": f"Weather Alert - {properties.get('event', 'Unknown')} - {airport_code}",
            "start_time_ms": start_ms,
            "end_time_ms": end_ms,
            "properties": {
                "airport": airport_code,
                "airportName": airport_name,
                "status": properties.get("status"),
                "headline": properties.get("headline"),
                "description": properties.get("description")
            }
        }

        # Remove empty or None properties
        evt["properties"] = {k: v for k, v in evt["properties"].items() if v}
        return evt

    def to_millis(self, iso_string: Optional[str]) -> Optional[int]:
        """
        Converts an ISO8601 string to epoch millis. Returns None if parse fails or string is empty.
        """
        if not iso_string:
            return None
        try:
            dt_obj = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            return int(dt_obj.timestamp() * 1000)
        except Exception:
            return None

    async def async_query_metrics(self) -> None:
        """
        Asynchronously fetches weather forecast data and station observations for each hub.
        """
        self.logger.info("async_query_metrics() started.")
        metrics_endpoint = f"{self.dynatrace_base_url}/api/v2/metrics/ingest"
        dynatrace_headers = {
            "Authorization": f"Api-Token {self.dynatrace_token}",
            "Content-Type": "text/plain",
            "User-Agent": "WeatherExtension"
        }

        # Limit concurrency with a semaphore
        semaphore = asyncio.Semaphore(self.thread_count)

        async with aiohttp.ClientSession() as session:
            tasks = []
            for airport_code, hub_info in self.united_hubs.items():
                tasks.append(asyncio.create_task(
                    self.process_metrics_for_hub(
                        session, semaphore, airport_code, hub_info, metrics_endpoint, dynatrace_headers
                    )
                ))
            await asyncio.gather(*tasks)

        self.logger.info("async_query_metrics() ended.")

    async def process_metrics_for_hub(self, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore,
                                      airport_code: str, hub_info: Dict[str, str],
                                      endpoint: str, headers: Dict[str, str]) -> None:
        async with semaphore:
            try:
                self.logger.info(f"Processing metrics for {airport_code}...")
                latitude = float(hub_info.get("lat"))
                longitude = float(hub_info.get("lon"))

                # Get location metadata
                metadata = await self.async_get_location_metadata(session, latitude, longitude)
                if not metadata:
                    self.logger.error(f"Unable to fetch location metadata for {airport_code}.")
                    return

                props = metadata.get("properties", {})
                grid_id = props.get("gridId")
                grid_x = props.get("gridX")
                grid_y = props.get("gridY")
                station_id = props.get("stationIdentifier")

                if not all([grid_id, grid_x, grid_y]):
                    self.logger.error(f"Unable to fetch grid details for {airport_code}.")
                    return

                # Parallel calls for forecast, hourly forecast, and station observations
                forecast, hourly_forecast, observations = await asyncio.gather(
                    self.async_get_weather_forecast(session, grid_id, grid_x, grid_y),
                    self.async_get_hourly_forecast(session, grid_id, grid_x, grid_y),
                    self.async_get_station_observations(session, station_id) if station_id else asyncio.sleep(0)
                )

                all_metrics = []

                # Daily forecast
                if forecast:
                    all_metrics.extend(self.format_weather_data_as_metrics(airport_code, forecast))

                # Hourly forecast
                if hourly_forecast:
                    all_metrics.extend(self.format_hourly_metrics(airport_code, hourly_forecast))

                # Observations
                if observations and isinstance(observations, dict):
                    all_metrics.extend(self.format_observation_metrics(airport_code, observations))

                # Print out the metrics before sending
                for line in all_metrics:
                    print(f"[DEBUG] Metric to be sent: {line}")

                # Send to Dynatrace
                if all_metrics:
                    await self.async_post_metrics_to_dynatrace(session, all_metrics, endpoint, headers)
                else:
                    self.logger.info(f"No metric data for {airport_code}.")
            except Exception as e:
                self.logger.error(f"Error processing metrics for {airport_code}: {e}")

    async def async_get_location_metadata(self, session: aiohttp.ClientSession,
                                          latitude: float, longitude: float) -> Optional[Dict]:
        url = f"https://api.weather.gov/points/{latitude},{longitude}"
        try:
            async with session.get(url, headers={"User-Agent": "WeatherExtension"}) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    self.logger.error(f"Error fetching metadata [{response.status}] for {latitude},{longitude}")
                    return None
        except Exception as e:
            self.logger.error(f"Exception in async_get_location_metadata: {e}")
            return None

    async def async_get_weather_forecast(self, session: aiohttp.ClientSession,
                                         grid_id: str, grid_x: int, grid_y: int) -> Optional[Dict]:
        url = f"https://api.weather.gov/gridpoints/{grid_id}/{grid_x},{grid_y}/forecast"
        try:
            async with session.get(url, headers={"User-Agent": "WeatherExtension"}) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    self.logger.error(f"Error fetching forecast [{response.status}] for {grid_id} {grid_x},{grid_y}")
                    return None
        except Exception as e:
            self.logger.error(f"Exception in async_get_weather_forecast: {e}")
            return None

    async def async_get_hourly_forecast(self, session: aiohttp.ClientSession,
                                        grid_id: str, grid_x: int, grid_y: int) -> Optional[Dict]:
        url = f"https://api.weather.gov/gridpoints/{grid_id}/{grid_x},{grid_y}/forecast/hourly"
        try:
            async with session.get(url, headers={"User-Agent": "WeatherExtension"}) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    self.logger.error(f"Error fetching hourly forecast [{response.status}] for {grid_id} {grid_x},{grid_y}")
                    return None
        except Exception as e:
            self.logger.error(f"Exception in async_get_hourly_forecast: {e}")
            return None

    async def async_get_station_observations(self, session: aiohttp.ClientSession,
                                             station_id: str) -> Optional[Dict]:
        """
        Fetch latest observation from a station if station ID is known.
        """
        url = f"https://api.weather.gov/stations/{station_id}/observations/latest"
        try:
            async with session.get(url, headers={"User-Agent": "WeatherExtension"}) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    self.logger.error(f"Error fetching station observations [{response.status}] for station {station_id}")
                    return None
        except Exception as e:
            self.logger.error(f"Exception in async_get_station_observations: {e}")
            return None

    async def async_post_metrics_to_dynatrace(self, session: aiohttp.ClientSession,
                                              metrics: List[str], endpoint: str, headers: Dict[str, str]) -> None:
        if not metrics:
            return
        payload = "\n".join(metrics)
        try:
            async with session.post(endpoint, headers=headers, data=payload) as resp:
                if resp.status == 202:
                    self.logger.info("Metrics successfully ingested.")
                else:
                    self.logger.error(f"Error ingesting metrics: {resp.status}, {await resp.text()}")
        except Exception as e:
            self.logger.error(f"Exception posting metrics to Dynatrace: {e}")

    def format_weather_data_as_metrics(self, airport_code: str, forecast: dict) -> List[str]:
        """
        Formats the daily forecast periods into Dynatrace metric lines.
        """
        metrics = []
        periods = forecast.get("properties", {}).get("periods", [])
        sanitized_code = self._sanitize_value(airport_code)

        for period in periods:
            period_name = period.get("name", "Unknown")
            short_forecast = period.get("shortForecast", "Unknown")
            wind_speed = period.get("windSpeed", "")
            wind_direction = period.get("windDirection", "")
            temperature = period.get("temperature")
            prob_of_precip = period.get("probabilityOfPrecipitation", {}).get("value")
            humidity = period.get("relativeHumidity", {}).get("value")

            base_dimensions = self._get_base_dimensions(
                sanitized_code, period_name, wind_speed, short_forecast, wind_direction
            )

            # Temperature
            if temperature is not None:
                if self.temperature_unit in (2, 3):  # Celsius
                    metrics.append(f"weather.temperature.celsius,{base_dimensions} {temperature}")
                if self.temperature_unit in (1, 3):  # Fahrenheit
                    fahrenheit = temperature * 9/5 + 32
                    metrics.append(f"weather.temperature.fahrenheit,{base_dimensions} {fahrenheit:.1f}")

            # Wind Speed
            if wind_speed:
                numeric = re.findall(r"\d+", wind_speed)
                if numeric:
                    try:
                        wind_speed_value = int(numeric[0])
                        metrics.append(f"weather.windSpeed,{base_dimensions} {wind_speed_value}")
                    except ValueError:
                        pass

            # Relative Humidity
            if humidity is not None:
                metrics.append(f"weather.relativeHumidity,{base_dimensions} {humidity}")

            # Probability of Precipitation
            if prob_of_precip is not None:
                metrics.append(f"weather.precipitationProbability,{base_dimensions} {prob_of_precip}")

        return metrics

    def format_hourly_metrics(self, airport_code: str, forecast: dict) -> List[str]:
        """
        Formats hourly forecast periods into Dynatrace metric lines.
        """
        metrics = []
        periods = forecast.get("properties", {}).get("periods", [])
        sanitized_code = self._sanitize_value(airport_code)

        for period in periods:
            period_name = period.get("name", "Unknown")
            short_forecast = period.get("shortForecast", "Unknown")
            wind_speed = period.get("windSpeed", "")
            wind_direction = period.get("windDirection", "")
            temperature = period.get("temperature")
            prob_of_precip = period.get("probabilityOfPrecipitation", {}).get("value")
            humidity = period.get("relativeHumidity", {}).get("value")

            base_dimensions = self._get_base_dimensions(
                sanitized_code, period_name, wind_speed, short_forecast, wind_direction
            )

            # Hourly Temperature
            if temperature is not None:
                if self.temperature_unit in (2, 3):
                    metrics.append(f"weather.hourly.temperature.celsius,{base_dimensions} {temperature}")
                if self.temperature_unit in (1, 3):
                    fahrenheit = temperature * 9/5 + 32
                    metrics.append(f"weather.hourly.temperature.fahrenheit,{base_dimensions} {fahrenheit:.1f}")

            # Probability of Precipitation
            if prob_of_precip is not None:
                metrics.append(f"weather.hourly.precipitationProbability,{base_dimensions} {prob_of_precip}")

            # Relative Humidity
            if humidity is not None:
                metrics.append(f"weather.hourly.relativeHumidity,{base_dimensions} {humidity}")

            # Wind Speed
            if wind_speed:
                numeric = re.findall(r"\d+", wind_speed)
                if numeric:
                    try:
                        wind_speed_value = int(numeric[0])
                        metrics.append(f"weather.hourly.windSpeed,{base_dimensions} {wind_speed_value}")
                    except ValueError:
                        pass

        return metrics

    def format_observation_metrics(self, airport_code: str, observations: dict) -> List[str]:
        """
        Formats station observations into Dynatrace metric lines.
        """
        metrics = []
        properties = observations.get("properties", {})
        sanitized_code = self._sanitize_value(airport_code)

        base_dimensions = f"airport_code={sanitized_code}"

        # Observed Temperature
        temp_c = properties.get("temperature", {}).get("value")
        if temp_c is not None:
            if self.temperature_unit in (2, 3):
                metrics.append(f"weather.observed.temperature.celsius,{base_dimensions} {temp_c}")
            if self.temperature_unit in (1, 3):
                temp_f = temp_c * 9/5 + 32
                metrics.append(f"weather.observed.temperature.fahrenheit,{base_dimensions} {temp_f:.1f}")

        # Observed Wind Speed
        wind_speed = properties.get("windSpeed", {}).get("value")
        if wind_speed is not None:
            metrics.append(f"weather.observed.windSpeed,{base_dimensions} {wind_speed}")

        # Observed Relative Humidity
        humidity = properties.get("relativeHumidity", {}).get("value")
        if humidity is not None:
            metrics.append(f"weather.observed.relativeHumidity,{base_dimensions} {humidity}")

        # Visibility
        visibility = properties.get("visibility", {}).get("value")
        if visibility is not None:
            metrics.append(f"weather.observed.visibility,{base_dimensions} {visibility}")

        # Barometric Pressure (converted to hPa/mb from Pa)
        pressure = properties.get("barometricPressure", {}).get("value")
        if pressure is not None:
            pressure_mb = pressure / 100
            metrics.append(f"weather.observed.pressure,{base_dimensions} {pressure_mb:.1f}")

        return metrics

    def _get_base_dimensions(self, airport_code: str, period_name: str,
                             wind_speed: str, forecast: str, wind_direction: str) -> str:
        sanitized_period = self._sanitize_value(period_name)
        sanitized_wind_speed = self._sanitize_value(wind_speed)
        wind_condition = self._sanitize_value(self._parse_wind_condition(sanitized_wind_speed))
        day_part = self._sanitize_value(self._parse_day_part(period_name))
        condition_type = self._sanitize_value(self._parse_condition(forecast))
        sanitized_wind_direction = self._sanitize_value(wind_direction)

        return (
            f"airport_code={airport_code},"
            f"forecast_period={sanitized_period},"
            f"day_part={day_part},"
            f"wind_condition={wind_condition},"
            f"weather_condition={condition_type},"
            f"wind_direction={sanitized_wind_direction}"
        )

    def _sanitize_value(self, value: str) -> str:
        """
        Replaces spaces with underscores and removes invalid dimension characters.
        """
        if not value:
            return ""
        value = re.sub(r"\s+", "_", value)
        value = re.sub(r"[^a-zA-Z0-9_.:-]", "", value)
        return value

    def _parse_wind_condition(self, wind_speed_str: str) -> str:
        numeric_part = re.findall(r"\d+", wind_speed_str)
        if not numeric_part:
            return "Unknown"
        try:
            speed = int(numeric_part[0])
        except ValueError:
            return "Unknown"
        if speed < 5:
            return "Calm"
        elif speed <= 15:
            return "Breezy"
        else:
            return "Gusty"

    def _parse_day_part(self, period_name: str) -> str:
        lower_name = period_name.lower()
        if "night" in lower_name or "tonight" in lower_name:
            return "Night"
        return "Day"

    def _parse_condition(self, short_forecast: str) -> str:
        lower_forecast = short_forecast.lower()
        if "rain" in lower_forecast:
            return "Rain"
        elif "snow" in lower_forecast:
            return "Snow"
        elif "sunny" in lower_forecast:
            return "Sunny"
        elif "cloudy" in lower_forecast:
            return "Cloudy"
        return "Other"

def main():
    ExtensionImpl(name="weather").run()

if __name__ == "__main__":
    main()

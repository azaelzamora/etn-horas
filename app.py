import os
import requests
from datetime import datetime, timedelta
import pytz
from flask import Flask, jsonify, send_from_directory, request
from apscheduler.schedulers.background import BackgroundScheduler
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# API configuration
FLEET_BASE_URL = "https://api.samsara.com/fleet"
V1_BASE_URL = "https://api.samsara.com/v1"
HEADERS = {
    "accept": "application/json",
    "authorization": "Bearer " + os.environ.get('SAMSARA_API_KEY', 'your_api_key_here')
}

# Set a threshold for ongoing trips (e.g., 100 years from now in milliseconds)
ONGOING_TRIP_THRESHOLD = int((datetime.now() + timedelta(days=36500)).timestamp() * 1000)
MEXICO_TZ = pytz.timezone('America/Mexico_City')

# Global variable to store the latest driver hours data
latest_driver_hours = {}

def get_all_vehicles():
    url = f"{FLEET_BASE_URL}/vehicles"
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        data = response.json()
        return [str(vehicle['id']) for vehicle in data['data']]
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching vehicles: {e}")
        return []

def get_time_range(hours):
    end_time = datetime.now(MEXICO_TZ)
    start_time = end_time - timedelta(hours=hours)
    return int(start_time.timestamp() * 1000), int(end_time.timestamp() * 1000)

def get_trips(vehicle_id, start_ms, end_ms):
    url = f"{V1_BASE_URL}/fleet/trips"
    params = {
        "vehicleId": vehicle_id,
        "startMs": start_ms,
        "endMs": end_ms
    }
    try:
        response = requests.get(url, headers=HEADERS, params=params)
        response.raise_for_status()
        return response.json().get('trips', [])
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching trips for vehicle {vehicle_id}: {e}")
        return []

def get_driver_name(driver_id):
    url = f"{FLEET_BASE_URL}/drivers/{driver_id}"
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        driver_data = response.json()
        return driver_data['data'].get('name', f"Unknown Driver ({driver_id})")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching driver name for ID {driver_id}: {e}")
        return f"Unknown Driver ({driver_id})"

def calculate_driver_hours(vehicles, start_ms, end_ms):
    driver_times = {}
    unknown_driver_time = 0
    ongoing_trips = {}
    unknown_ongoing_trips = 0

    for vehicle_id in vehicles:
        trips = get_trips(vehicle_id, start_ms, end_ms)
        for trip in trips:
            driver_id = trip.get('driverId')
            trip_start = trip['startMs']
            trip_end = trip['endMs']
            
            is_ongoing = trip_end > ONGOING_TRIP_THRESHOLD
            if is_ongoing:
                trip_end = end_ms
            
            duration = (trip_end - trip_start) / 1000 / 3600  # Convert to hours
            
            if driver_id:
                driver_times[driver_id] = driver_times.get(driver_id, 0) + duration
                if is_ongoing:
                    ongoing_trips[driver_id] = ongoing_trips.get(driver_id, 0) + 1
            else:
                unknown_driver_time += duration
                if is_ongoing:
                    unknown_ongoing_trips += 1

    return driver_times, unknown_driver_time, ongoing_trips, unknown_ongoing_trips

def format_time(hours):
    total_minutes = int(hours * 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours} hours {minutes} minutes"

def update_driver_hours(hours=24):
    vehicles = get_all_vehicles()
    logging.info(f"Found {len(vehicles)} vehicles")
    start_ms, end_ms = get_time_range(hours)
    
    driver_times, unknown_driver_time, ongoing_trips, unknown_ongoing_trips = calculate_driver_hours(vehicles, start_ms, end_ms)
    
    logging.info(f"Calculated hours for {len(driver_times)} drivers")
    
    formatted_data = []
    for driver_id, hours in driver_times.items():
        driver_name = get_driver_name(driver_id)
        formatted_time = format_time(hours)
        ongoing = ongoing_trips.get(driver_id, 0)
        formatted_data.append({
            "name": driver_name,
            "time": formatted_time,
            "ongoing": ongoing
        })
    
    if unknown_driver_time > 0:
        formatted_data.append({
            "name": "Conductor Desconocido",
            "time": format_time(unknown_driver_time),
            "ongoing": unknown_ongoing_trips
        })
    
    return {
        "data": sorted(formatted_data, key=lambda x: x['time'], reverse=True),
        "last_updated": datetime.now(MEXICO_TZ).strftime("%Y-%m-%d %H:%M:%S")
    }

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/driver-hours')
def get_driver_hours():
    hours = int(request.args.get('hours', 24))
    return jsonify(update_driver_hours(hours))

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(lambda: update_driver_hours(24), 'interval', minutes=5)
scheduler.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
from multiprocessing import Process, Manager
from skyfield.api import load, Topos
from scipy.optimize import brentq
from skyfield.api import utc
import threading
import datetime
import keyboard
import winsound
import heapq
import time
import os

def time_left_below_30_degrees(satellite, topos, ts):
    # Generate a list of datetime objects from now to 8 minutes from now, in 1-second intervals
    now_utc = datetime.datetime.utcnow().replace(tzinfo=utc)
    future_utc = (datetime.datetime.utcnow() + datetime.timedelta(minutes=8)).replace(tzinfo=utc)
    
    dt_list = [now_utc + datetime.timedelta(seconds=x) for x in range(0, int((future_utc - now_utc).total_seconds()))]
    times = ts.utc(dt_list)
    
    altitudes = ((satellite - topos).at(times).altaz()[0]).degrees
    
    for i in range(1, len(altitudes)):
        if altitudes[i] < 30 and altitudes[i-1] >= 30:
            def f(t):
                time = ts.tt(jd=t)
                alt = (satellite - topos).at(time).altaz()[0].degrees
                return alt - 30.0
            
            root_time = brentq(f, times[i-1].tt, times[i].tt)
            exact_time = ts.tt(jd=root_time).utc_datetime()
            return exact_time
    return None

# ANSI color codes
RESET = "\033[0m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"

# Clear the screen
def clear_screen():
    if os.name == 'posix':
        os.system('clear')
    else:
        os.system('cls')

# Worker function to update satellite positions
def update_satellite_positions(satellite_queue):
    try:
        stations_url = 'http://www.celestrak.com/NORAD/elements/starlink.txt'
        satellites = load.tle_file(stations_url)
        topos = Topos(latitude=32.46844660231615, longitude=-94.72433544749386)
        ts = load.timescale()

        while True:
            updated_satellites = []
            t = ts.now()
            for satellite in satellites:
                astrometric = (satellite - topos).at(t)
                alt, az, _ = astrometric.altaz()
                updated_satellites.append((satellite.name, alt.degrees, az.degrees))
            satellite_queue.put(updated_satellites)
    except BrokenPipeError:
        pass

# Find corrected Azimuth value
def normalize_angle_difference(diff):
    if diff < -180:
        return diff + 360
    elif diff > 180:
        return diff - 360
    else:
        return diff
    
# Function to find the closest satellite to the currently tracked one
def find_closest_satellite(current_sat, satellite_data, tracked_sats, satellite_dict, topos, ts):
    closest_sat = None
    closest_distance = float('inf')
    current_sat_name, current_alt, current_az = current_sat
    delta_alt = 0
    delta_az = 0

    for sat_name, alt, az in satellite_data:
        if sat_name in tracked_sats or alt <= 30:
            continue
        distance = ((current_alt - alt)**2 + (current_az - az)**2)**0.5
        if distance < closest_distance:
            closest_distance = distance
            closest_sat = (sat_name, alt, az)
            delta_alt = alt - current_alt
            delta_az = normalize_angle_difference(az - current_az)

    time_left = time_left_below_30_degrees(satellite_dict[closest_sat[0]], topos, ts) if closest_sat else None
    return closest_sat, closest_distance, delta_alt, delta_az, time_left

# Function to find the center satellite in a cluster of 6 satellites that are closest to each other
def find_center_satellite(satellite_data, satellite_dict, topos, ts):
    closest_clusters = []
    for sat1 in satellite_data:
        name1, alt1, az1 = sat1
        closest_neighbors = []
        for sat2 in satellite_data:
            if sat1 == sat2:
                continue
            name2, alt2, az2 = sat2
            distance = ((alt1 - alt2)**2 + (az1 - az2)**2)**0.5
            heapq.heappush(closest_neighbors, (distance, sat2))
        closest_neighbors = heapq.nsmallest(5, closest_neighbors)
        sum_distances = sum(distance for distance, _ in closest_neighbors)
        heapq.heappush(closest_clusters, (sum_distances, sat1, closest_neighbors))

    _, center_satellite, _ = heapq.nsmallest(1, closest_clusters)[0]
    time_left = time_left_below_30_degrees(satellite_dict[center_satellite[0]], topos, ts)
    return center_satellite, time_left

# Function to play a beep sound using winsound
def play_beep(frequency, duration):
    winsound.Beep(frequency, duration)

# Function to play a beep sound using threading
def threaded_beep(frequency, duration):
    beep_thread = threading.Thread(target=play_beep, args=(frequency, duration))
    beep_thread.start()

def main():
    # Clear the screen
    clear_screen()
    print(f"{YELLOW}Loading TLE Data...{RESET}")
    stations_url = 'http://www.celestrak.com/NORAD/elements/starlink.txt'
    satellites = load.tle_file(stations_url)
    satellite_dict = {sat.name: sat for sat in satellites}
    topos = Topos(latitude=32.46844660231615, longitude=-94.72433544749386)
    ts = load.timescale()

    manager = Manager()
    satellite_queue = manager.Queue()
    worker_process = Process(target=update_satellite_positions, args=(satellite_queue,))
    worker_process.start()
    
    tracked_satellite = None
    tracked_sats = set()
    space_pressed = False
    reference_point = None  # Reference point when no eligible satellite is available
    satellite_data = []  # Initialize satellite_data
    tracking_start_time = None  # Initialize tracking start time
    end_time = None  # Initialize time_left
    
    print(f"{CYAN}Loading Complete. Begin tracking.{RESET}")
    while True:
        # Check for Escape key to exit
        if keyboard.is_pressed('esc'):
            print(f"\n{RED}Exiting program.{RESET}")
            break

        curr_time = time.time()

        # Update the global satellite locations if the background thread offers
        if not satellite_queue.empty():
            satellite_data = satellite_queue.get()
            
        # Initialize tracked_satellite on first run
        if tracked_satellite is None:
            eligible_sats = [sat for sat in satellite_data if sat[1] > 30]
            if eligible_sats:
                tracked_satellite, end_time = find_center_satellite(eligible_sats, satellite_dict, topos, ts)
                tracked_sats.add(tracked_satellite[0])  # Add the initially tracked satellite to the set
                tracking_start_time = curr_time  # Set the tracking start time
                threaded_beep(750, 100)  # Play success chime for initial tracking

        # Manual switch attempt to new satellite
        if keyboard.is_pressed('space'):
            if not space_pressed and tracked_satellite:
                space_pressed = True
                closest_sat, _, delta_alt, delta_az, new_end_time = find_closest_satellite(tracked_satellite, [sat for sat in satellite_data if sat[1] > 30], tracked_sats, satellite_dict, topos, ts)
                if closest_sat:
                    tracked_sats.add(tracked_satellite[0])
                    tracked_sats.add(closest_sat[0])
                    tracked_satellite = closest_sat
                    tracking_start_time = curr_time  # Reset the tracking start time
                    end_time = new_end_time  # Update the end_time only if closest_sat is not None
                    time_left_seconds = 0.0  # Default value
                    if new_end_time is not None:
                        time_left_seconds = (new_end_time - datetime.datetime.utcnow().replace(tzinfo=utc)).total_seconds()
                    print(f"\nSwitched to {closest_sat[0]} | {CYAN}{closest_sat[1]:.3f}°, {closest_sat[2]:.3f}°{RESET} | {GREEN}Δ{delta_alt:+.3f}°, Δ{delta_az:+.3f}°{RESET} | Time left: {time_left_seconds:.2f} seconds")
                    threaded_beep(750, 100)  # Play success chime
                else:
                    # Play error chime if no eligible satellite to switch to
                    threaded_beep(1000, 100)
        else:
            space_pressed = False

        total_count = len(satellite_data)
        count_above_30 = sum(1 for _, alt, _ in satellite_data if alt > 30)
        count_not_tracked = sum(1 for name, alt, _ in satellite_data if alt > 30 and name not in tracked_sats)
        count_tracked = len(tracked_sats)  # Number of unique satellites that have been tracked

        # Main tracking process and print output
        if tracked_satellite:
            sat_name = tracked_satellite[0]
            # Update the altitude and azimuth
            t = ts.now()
            astrometric = (satellite_dict[sat_name] - topos).at(t)
            alt, az, _ = astrometric.altaz()

            if alt.degrees < 30:
                closest_sat, closest_distance, delta_alt, delta_az, end_time = find_closest_satellite(tracked_satellite, [sat for sat in satellite_data if sat[1] > 30], tracked_sats, satellite_dict, topos, ts)
                if closest_sat:
                    tracked_sats.add(tracked_satellite[0])
                    tracked_sats.add(closest_sat[0])
                    tracked_satellite = closest_sat
                    tracking_start_time = curr_time  # Reset the tracking start time
                    time_left_seconds = 0.0  # Default value
                    if end_time is not None:
                        time_left_seconds = (end_time - datetime.datetime.utcnow().replace(tzinfo=utc)).total_seconds()
                    print(f"\nSwitched to {closest_sat[0]} | {CYAN}{closest_sat[1]:.3f}°, {closest_sat[2]:.3f}°{RESET} | {GREEN}Δ{delta_alt:+.3f}°, Δ{delta_az:+.3f}°{RESET} | Time left: {time_left_seconds:.2f} seconds")
                    threaded_beep(750, 100)  # Play success chime
                else:
                    reference_point = (sat_name, alt.degrees, az.degrees)
                    tracked_satellite = None
                    print(f"\nNo longer tracking {sat_name} due to low altitude, no eligible satellite available. Holding at reference point: {reference_point}")
            else:
                elapsed_time = curr_time - tracking_start_time
                now_utc = datetime.datetime.utcnow().replace(tzinfo=utc)
        
                # Calculate the time left
                if end_time is not None:
                    time_left = (end_time - now_utc).total_seconds()
                    time_left = max(0, time_left)  # Ensure time_left is never negative
                    print(f"\r{GREEN}Tracking {sat_name}{RESET} | {BLUE}Altitude: {alt.degrees:.3f}°{RESET} | {MAGENTA}Azimuth: {az.degrees:.3f}°{RESET} | {CYAN}Tracked for: {elapsed_time:.2f}s{RESET} | {RED}Time Left: {time_left:.2f}s{RESET} | {YELLOW}Sats above 30°: {count_not_tracked}/{count_above_30} ({count_tracked}/{total_count}){RESET}\033[K", end='', flush=True)
                else:
                    print(f"\r{GREEN}Tracking {sat_name}{RESET} | {BLUE}Altitude: {alt.degrees:.3f}°{RESET} | {MAGENTA}Azimuth: {az.degrees:.3f}°{RESET} | {CYAN}Tracked for: {elapsed_time:.2f}s{RESET} | {YELLOW}Sats above 30°: {count_not_tracked}/{count_above_30} ({count_tracked}/{total_count}){RESET}\033[K", end='', flush=True)
        elif reference_point:
            print(f"\r{YELLOW}Holding at reference point: {reference_point}{RESET}\033[K", end='', flush=True)

if __name__ == "__main__":
    main()

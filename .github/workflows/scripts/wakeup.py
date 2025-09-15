# app/.github/scripts/wakeup.py

import os
import requests
import datetime
import pytz

# --- Your Business Rules ---
# Monday (0) to Friday (4)
BUSINESS_WEEKDAYS = [0, 1, 2, 3, 4] 
# 6:00 AM to 8:59 AM, and 3:00 PM to 5:59 PM
BUSINESS_HOURS = [(6, 9), (15, 18)] 
# --- End of Rules ---

def run_check():
    """
    Checks the time in US Central and pings a URL if within business hours.
    """
    url = os.environ.get('RENDER_URL')
    if not url:
        print('Error: RENDER_URL secret is not set in GitHub repository settings.')
        exit(1)

    try:
        tz = pytz.timezone('America/Chicago')
        now = datetime.datetime.now(tz)
    except Exception as e:
        print(f'Error getting timezone: {e}')
        exit(1)

    print(f'Current Central Time: {now.strftime("%Y-%m-%d %H:%M:%S %Z%z")}')
    print(f'Current weekday (Mon=0): {now.weekday()}')
    print(f'Current hour: {now.hour}')

    if now.weekday() not in BUSINESS_WEEKDAYS:
        print('It is a weekend. No ping will be sent.')
        exit(0)

    is_active_time = False
    for start_hour, end_hour in BUSINESS_HOURS:
        if start_hour <= now.hour < end_hour:
            is_active_time = True
            break

    if not is_active_time:
        print('It is a weekday, but outside of business hours. No ping will be sent.')
        exit(0)

    print(f'Within business hours. Pinging {url} to keep it alive...')
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        print(f'Ping successful! Status code: {response.status_code}')
    except requests.exceptions.RequestException as e:
        print(f'Ping failed: {e}')
        exit(1)

if __name__ == '__main__':
    run_check()
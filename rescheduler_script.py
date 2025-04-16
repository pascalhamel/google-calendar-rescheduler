import datetime
import os
import logging
import argparse  # For command-line arguments
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pydantic import Field
from scoutsdk.api import ScoutAPI  # Assuming you have a ScoutAPI module for API interactions
from scoutsdk import scout
import pytz  # Add this import for timezone handling
import json

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Set up the ScoutAPI client
scout_api = ScoutAPI() # Ensure SCOUT_API_URL and SCOUT_API_ACCESS_TOKEN are set in your environment

def get_google_calendar_service():
    """Authenticates and returns a Google Calendar API service object."""
    creds = None

    # Check if GOOGLE_TOKEN_JSON environment variable exists
    google_token_json = scout.context.get('GOOGLE_TOKEN_JSON')
    if google_token_json:
        try:
            # Serialize the dictionary to a JSON string if it's not already a string
            if isinstance(google_token_json, dict):
                google_token_json = json.dumps(google_token_json)
            with open('token.json', 'w') as token_file:
                token_file.write(google_token_json)
            logging.info("GOOGLE_TOKEN_JSON environment variable found and written to token.json.")
        except Exception as e:
            logging.error(f"Failed to write GOOGLE_TOKEN_JSON to token.json: {e}")
            return None

    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', ['https://www.googleapis.com/auth/calendar'])
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow

            # Load client configuration from the environment variable
            client_config = scout.context.get("GOOGLE_CLIENT_CONFIG")
            if not client_config:
                logging.error("GOOGLE_CLIENT_CONFIG environment variable is not set.")
                return None

            try:
                client_config_dict = json.loads(client_config)
            except json.JSONDecodeError:
                logging.error("Invalid JSON format in GOOGLE_CLIENT_CONFIG.")
                return None

            flow = InstalledAppFlow.from_client_config(
                client_config_dict, ['https://www.googleapis.com/auth/calendar']
            )
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except HttpError as error:
        logging.error(f'An error occurred: {error}')
        return None


def get_meetings_to_reschedule(service, calendar_id, vacation_date):
    """Retrieves meetings owned by you on the specified vacation date."""
    meetings_to_reschedule = []
    start_of_day = datetime.datetime.combine(vacation_date, datetime.time.min).isoformat() + 'Z'
    end_of_day = datetime.datetime.combine(vacation_date, datetime.time.max).isoformat() + 'Z'

    try:
        events_result = service.events().list(calendarId=calendar_id,
                                              timeMin=start_of_day,
                                              timeMax=end_of_day,
                                              singleEvents=True,
                                              orderBy='startTime').execute()
        events = events_result.get('items', [])

        # Get the calendar information to determine the owner's email
        calendar = service.calendars().get(calendarId=calendar_id).execute()
        owner_email = calendar.get('id')

        logging.info(f"Found {len(events)} events on {vacation_date}")  # Log the number of events found

        for event in events:
            # Only include events with eventType: default
            if event.get('eventType') != 'default':
                logging.info(f"Skipping non-default event: {event.get('summary', 'No Summary')}")
                continue

            if event.get('organizer', {}).get('email') == owner_email:  # Check if you are the organizer
                meetings_to_reschedule.append(event)
                logging.info(f"Event '{event.get('summary', 'No Summary')}' added to reschedule list.") # Log when an event is added

    except HttpError as error:
        logging.error(f'An error occurred: {error}')

    return meetings_to_reschedule

def find_available_slot(service, calendar_id, new_date, meeting_duration_minutes, time_slot_start, time_slot_end, attendees_emails, timezone, reserved_slots):
    """Finds an available time slot on the given date, ensuring all attendees are free and avoiding reserved slots."""
    # Combine date and time, then localize to the calendar's timezone
    start_time = timezone.localize(datetime.datetime.combine(new_date, time_slot_start))
    end_time = timezone.localize(datetime.datetime.combine(new_date, time_slot_end))

    # Convert to RFC3339 format with timezone information
    start_time_iso = start_time.isoformat()
    end_time_iso = end_time.isoformat()

    logging.debug(f"Searching for available slots on {new_date} between {start_time} and {end_time}")
    logging.debug(f"Meeting duration: {meeting_duration_minutes} minutes")

    lunch_start = timezone.localize(datetime.datetime.combine(new_date, datetime.time(12, 0)))
    lunch_end = timezone.localize(datetime.datetime.combine(new_date, datetime.time(13, 0)))

    try:
        # Fetch events in the specified time range
        events_result = service.events().list(calendarId=calendar_id,
                                              timeMin=start_time_iso,
                                              timeMax=end_time_iso,
                                              singleEvents=True,
                                              orderBy='startTime').execute()
        events = events_result.get('items', [])
        logging.debug(f"Found {len(events)} events in the time range.")

        # Iterate through possible time slots
        current_time = start_time
        while current_time + datetime.timedelta(minutes=meeting_duration_minutes) <= end_time:
            slot_start = current_time
            slot_end = current_time + datetime.timedelta(minutes=meeting_duration_minutes)

            # Skip slots that overlap with lunch time
            if slot_start < lunch_end and slot_end > lunch_start:
                logging.debug(f"Skipping slot during lunch time: {slot_start} to {slot_end}")
                current_time += datetime.timedelta(minutes=15)
                continue

            # Check if the slot is already reserved
            if slot_start in reserved_slots:
                logging.debug(f"Skipping reserved slot: {slot_start}")
                current_time += datetime.timedelta(minutes=15)
                continue

            logging.debug(f"Checking slot: {slot_start} to {slot_end}")

            # Check if the slot conflicts with any existing events
            conflict = False
            for event in events:
                if event.get('transparency') == 'transparent':
                    logging.debug(f"Ignoring event (not marked busy): {event.get('summary', 'No Summary')}")
                    continue

                event_start = event['start'].get('dateTime', event['start'].get('date'))
                event_end = event['end'].get('dateTime', event['end'].get('date'))

                if slot_start.isoformat() < event_end and slot_end.isoformat() > event_start:
                    logging.debug(f"Conflict found with event: {event.get('summary', 'No Summary')}")
                    conflict = True
                    break

            if conflict:
                current_time += datetime.timedelta(minutes=15)
                continue

            # Check attendees' availability using freebusy.query
            freebusy_request = {
                "timeMin": slot_start.isoformat(),
                "timeMax": slot_end.isoformat(),
                "items": [{"id": email} for email in attendees_emails]
            }

            freebusy_result = service.freebusy().query(body=freebusy_request).execute()
            busy_calendars = [
                calendar_id for calendar_id, calendar_data in freebusy_result.get('calendars', {}).items()
                if calendar_data.get('busy')
            ]

            if busy_calendars:
                logging.debug(f"Conflict found for attendees: {', '.join(busy_calendars)}")
                current_time += datetime.timedelta(minutes=15)
                continue

            # If no conflicts and not reserved, return the available slot
            logging.debug(f"Available slot found: {slot_start}")
            return slot_start

        logging.warning("No available slot found.")
        return None  # No available slot found

    except HttpError as error:
        logging.error(f'An error occurred while searching for available slots: {error}')
        return None


def reschedule_meeting(service, calendar_id, event, new_start_time):
    """Reschedules a meeting to the given start time."""
    original_start_time = datetime.datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
    new_end_time = new_start_time + datetime.timedelta(minutes=get_meeting_duration(event))

    logging.info(f"Proposed change:")
    logging.info(f"  Meeting: {event['summary']}")
    logging.info(f"  Original Time: {original_start_time.strftime('%Y-%m-%d %H:%M')}")
    logging.info(f"  New Time: {new_start_time.strftime('%Y-%m-%d %H:%M')}")

    try:
        # Ensure dateTime fields are properly formatted with timezone information
        event['start']['dateTime'] = new_start_time.isoformat()
        event['end']['dateTime'] = new_end_time.isoformat()

        updated_event = service.events().update(calendarId=calendar_id, eventId=event['id'], body=event).execute()
        logging.info(f"Rescheduled meeting: {event['summary']} to {new_start_time}")
        return updated_event

    except HttpError as error:
        logging.error(f'An error occurred: {error}')
        return None

def get_meeting_duration(event):
    """Calculates the duration of a meeting in minutes."""
    start_time = datetime.datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
    end_time = datetime.datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
    duration = (end_time - start_time).total_seconds() / 60
    return int(duration)

@scout.function(description="Reschedule meetings for multiple vacation days")
def main(vacation_dates_str: str = Field(description="Comma-separated list of vacation dates"), 
         acceptable_dates_str: str = Field(description="Comma-separated list of acceptable dates"), 
         time_slot_start_str: str = Field(description="Earliest hour at which the meeting can be rescheduled"), 
         time_slot_end_str: str = Field(description="Latest hour at which the meeting can be rescheduled"),
         dry_run: bool = Field(default=True, description="If true, only output suggested changes without rescheduling, this is the default behavior")):
    """Main function to orchestrate the rescheduling process for multiple vacation days."""

    # Initialize an array to collect log messages
    log_messages = []

    def log(level, message):
        """Helper function to append log messages to the array."""
        log_messages.append(f"{level.upper()}: {message}")

    try:
        service = get_google_calendar_service()
        if not service:
            log("error", "Failed to get Google Calendar service.")
            return "\n".join(log_messages)

        # Fetch the calendar's timezone
        try:
            calendar = service.calendars().get(calendarId='primary').execute()
            calendar_timezone = calendar.get('timeZone', 'UTC')
            timezone = pytz.timezone(calendar_timezone)
            log("info", f"Using calendar timezone: {calendar_timezone}")
        except HttpError as error:
            log("error", f"Failed to fetch calendar timezone: {error}")
            return "\n".join(log_messages)

        # Split and parse vacation dates
        try:
            vacation_dates = [
                timezone.localize(datetime.datetime.strptime(date_str.strip(), '%Y-%m-%d')).date()
                for date_str in vacation_dates_str.split(',')
            ]
        except ValueError:
            log("error", "Invalid vacation dates format. Use a comma-separated list of YYYY-MM-DD.")
            return "\n".join(log_messages)

        # Parse acceptable dates for rescheduling
        try:
            acceptable_dates = [
                timezone.localize(datetime.datetime.strptime(date_str.strip(), '%Y-%m-%d')).date()
                for date_str in acceptable_dates_str.split(',')
            ]
        except ValueError:
            log("error", "Invalid acceptable dates format. Use a comma-separated list of YYYY-MM-DD.")
            return "\n".join(log_messages)

        # Parse time slot start and end in the calendar's timezone
        try:
            time_slot_start = timezone.localize(datetime.datetime.strptime(time_slot_start_str, '%H:%M')).time()
            time_slot_end = timezone.localize(datetime.datetime.strptime(time_slot_end_str, '%H:%M')).time()
        except ValueError:
            log("error", "Invalid time slot format. Use HH:MM.")
            return "\n".join(log_messages)

        reserved_slots = set()  # Track reserved slots to avoid conflicts

        # Process each vacation date
        for vacation_date in vacation_dates:
            meetings_to_reschedule = get_meetings_to_reschedule(service, 'primary', vacation_date)  # 'primary' is your main calendar
            log("info", f"Meetings to reschedule on {vacation_date}: {len(meetings_to_reschedule)}")

            if not meetings_to_reschedule:
                log("warning", f"No meetings found to reschedule on {vacation_date}.")
                continue

            for event in meetings_to_reschedule:
                attendees_emails = [attendee['email'] for attendee in event.get('attendees', []) if 'email' in attendee]

                logging.debug(f"Attempting to find an available slot for meeting: '{event.get('summary', 'No Summary')}' "
                              f"with attendees: {', '.join(attendees_emails)}")

                for new_date in acceptable_dates:
                    available_slot = find_available_slot(service, 'primary', new_date, get_meeting_duration(event), 
                                                         time_slot_start, time_slot_end, attendees_emails, timezone, reserved_slots)

                    if available_slot and available_slot not in reserved_slots:
                        # Use the available_slot directly as it is already timezone-aware
                        new_start_time = available_slot
                        reserved_slots.add(new_start_time)  # Mark the slot as reserved, even in dry run mode
                        if dry_run:
                            log("info", f"Dry run: Meeting '{event['summary']}' would be rescheduled to {new_start_time}")
                        else:
                            reschedule_meeting(service, 'primary', event, new_start_time)
                        break
                    elif available_slot in reserved_slots:
                        log("info", f"Slot {available_slot} is already reserved. Searching for another slot.")
                else:
                    log("warning", f"No available slot found for meeting: {event['summary']} on any of the acceptable dates.")

    except Exception as e:
        log("error", f"An unexpected error occurred: {e}")

    finally:
        # Ensure token.json is erased from disk
        if os.path.exists('token.json'):
            try:
                os.remove('token.json')
                log("info", "token.json has been successfully deleted.")
            except Exception as e:
                log("error", f"Failed to delete token.json: {e}")

        # Return all collected log messages as a single string
        return "\n".join(log_messages)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Reschedule Google Calendar meetings for a vacation day.')
    parser.add_argument('vacation_dates', type=str, help='Comma-separated list of vacation dates (YYYY-MM-DD)')
    parser.add_argument('acceptable_dates', type=str, help='Comma-separated list of acceptable reschedule dates (YYYY-MM-DD)')
    parser.add_argument('time_slot_start', type=str, help='Time slot start (HH:MM)')
    parser.add_argument('time_slot_end', type=str, help='Time slot end (HH:MM)')
    parser.add_argument('--dry-run', action='store_true', help='Only output suggested changes without rescheduling')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')

    args = parser.parse_args()

    # Configure logging level based on the --debug flag
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)

    print(main(args.vacation_dates, args.acceptable_dates, args.time_slot_start, args.time_slot_end, args.dry_run))

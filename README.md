# Rescheduler Script

This project is a Python script designed to reschedule Google Calendar meetings for specified vacation days. It uses the Google Calendar API to identify meetings and reschedule them to acceptable dates and time slots. It is meant to be used in the Scout platform

## Features
- Automatically identifies meetings on specified vacation days.
- Reschedules meetings to acceptable dates and time slots.
- Ensures no conflicts with existing events or attendees' availability.
- Supports dry-run mode to preview changes without making actual updates.

## Prerequisites
1. Python 3.7 or higher.
2. Required Python packages (install using `pip install -r requirements.txt`).
4. Environment variables:
   - `SCOUT_CONTEXT`: JSON string containing Google client configuration and Google token configuration.
   - `SCOUT_API_URL` and `SCOUT_API_ACCESS_TOKEN` for ScoutAPI integration.

## Installation
1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd rescheduler
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage
Run the script using the following command:
```bash
python rescheduler_script.py <vacation_dates> <acceptable_dates> <time_slot_start> <time_slot_end> [--dry-run] [--debug]
```

### Arguments
- `<vacation_dates>`: Comma-separated list of vacation dates (e.g., `2025-04-20,2025-04-21`).
- `<acceptable_dates>`: Comma-separated list of acceptable reschedule dates (e.g., `2025-04-22,2025-04-23`).
- `<time_slot_start>`: Earliest time for rescheduling (e.g., `09:00`).
- `<time_slot_end>`: Latest time for rescheduling (e.g., `17:00`).

### Options
- `--dry-run`: Preview changes without making actual updates.
- `--debug`: Enable debug logging for detailed output.

### Example
```bash
python rescheduler_script.py "2025-04-20,2025-04-21" "2025-04-22,2025-04-23" "09:00" "17:00" --dry-run
```

## Logging
Logs are displayed in the console and include information about:
- Meetings found on vacation days.
- Rescheduling actions.
- Errors or warnings.

## Notes
- Ensure the proper environment variables are set.
- The script will generate a `token.json` file after the first run for subsequent authentications.
- Use the `--dry-run` option to preview changes before applying them.

## License
This project is licensed under the MIT License.
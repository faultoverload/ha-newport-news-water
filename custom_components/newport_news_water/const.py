"""Constants for the Newport News Waterworks integration."""

from datetime import timedelta
from zoneinfo import ZoneInfo

DOMAIN = "newport_news_water"

CONF_USER_AGENT = "user_agent"
CONF_METER_NUMBER = "meter_number"

# The portal only serves Newport News, VA; its dates are local calendar days.
PORTAL_TZ = ZoneInfo("America/New_York")

UPDATE_INTERVAL = timedelta(hours=6)

# How far back the first refresh pages through daily history.
MAX_BACKFILL_DAYS = 3 * 365
# Days re-imported on every refresh so late or revised readings get corrected.
REFRESH_WINDOW_DAYS = 31

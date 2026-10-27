# Newport News Waterworks for Home Assistant

[![hacs][hacs-badge]][hacs]
[![Validate][validate-badge]][validate]

A custom integration that pulls your daily water usage and estimated cost from the
[Newport News Waterworks customer portal](https://myservices.nnww.nnva.gov/NNWW/)
into Home Assistant, so you can track water in the
[Energy dashboard](https://www.home-assistant.io/docs/energy/water/).

This is an unofficial project; it is not affiliated with or endorsed by the City of
Newport News or Newport News Waterworks.

## What you get

- **Long-term statistics** for daily water consumption (gallons) and estimated cost (USD).
  The first refresh backfills everything the portal has (about two years). After that, each
  refresh re-imports the last 62 days, so late or corrected readings and newly posted bills
  fix themselves.
- **Sensors**
  - Latest day usage and estimated cost, plus the date of that reading
  - Month-to-date usage and estimated cost, and projected usage and cost for the month
  - Last bill usage and amount

The portal publishes a day's reading the following day, so the newest data is always from
yesterday. The integration checks every 6 hours.

## Installation

### HACS

1. In HACS, open the menu → **Custom repositories**, add
   `https://github.com/faultoverload/ha-newport-news-water` with category **Integration**.
2. Install **Newport News Waterworks** and restart Home Assistant.

### Manual

Copy `custom_components/newport_news_water` into your Home Assistant `config/custom_components`
folder and restart.

## Setup

1. **Settings → Devices & services → Add integration → Newport News Waterworks.**
2. Enter the username and password you use on myservices.nnww.nnva.gov.
3. The portal emails a 6-digit code the first time it sees a new device. Enter it when asked.

After that, the portal remembers Home Assistant and logs in without a code. If it ever asks
again (for example after a password change), Home Assistant shows a **Reauthenticate**
notice; open it and enter the new code that gets emailed to you.

## Energy dashboard

**Settings → Dashboards → Energy → Water consumption → Add water source**, then choose the
statistic **Newport News Water _meter_ consumption**. For cost, choose **Use an entity
tracking the total costs** and pick **Newport News Water _meter_ cost**.

The data arrives a day late, so the Energy dashboard's "today" view stays empty; use the day,
week or month views.

## Notes

- **Cost is an estimate.** The portal's daily "dollar" view actually reports cubic feet, so
  it can't be used. Instead, each bill in the portal's monthly view gives a rate (bill amount
  ÷ billed HCF, which spreads the fixed charge over your usage), and each day is priced at the
  rate of the bill that covers it. The portal doesn't give billing-period dates; matching
  bills against daily reads shows a bill for month M covers roughly the 11th of the previous
  month through the 10th of M, so that's what the estimate assumes. Days since the last
  bill use the last bill's rate. Over two years of one account's history the estimate came
  within 2% of the billed total; individual months can be off by 5–25% when meter-read dates
  shift.
- Accounts with more than one water meter use the first meter for now.
- Your password is stored in Home Assistant's config entry, like other cloud integrations.
  Nothing is sent anywhere except the Newport News Waterworks portal.

## Development

```bash
uv venv --python 3.14
uv pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

Tests use a fake portal with made-up data (`tests/portal.py`). To try the client against
the real portal, copy `.env.example` to `.env` and fill it in. `.env` and `captures/` are
git-ignored; never commit real portal responses, since they include names and addresses.

[hacs]: https://hacs.xyz
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[validate]: https://github.com/faultoverload/ha-newport-news-water/actions/workflows/validate.yml
[validate-badge]: https://github.com/faultoverload/ha-newport-news-water/actions/workflows/validate.yml/badge.svg

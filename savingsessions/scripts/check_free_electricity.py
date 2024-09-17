import json
import re
import urllib.request

import dateparser
import pytz

from .. import db


def main():
    resp = urllib.request.urlopen("https://octopus.energy/free-electricity/")
    body = resp.read().decode("utf-8")
    if m := re.search(r"⚡️\s*\b(.+) (\d+)([ap]m)?-(\d+)([ap]m)\b\s*⚡️", body):
        if m.group(3):
            date_from = m.expand(r"\1 \2\3")
        else:
            date_from = m.expand(r"\1 \2\5")
        date_to = m.expand(r"\1 \4\5")
        date_from = dateparser.parse(date_from)
        assert date_from
        date_to = dateparser.parse(date_to)
        assert date_to
        duration = date_to - date_from
        hh = int(duration.seconds / 1800)
        date_from = pytz.timezone("Europe/London").localize(date_from)
        row = dict(timestamp=date_from.isoformat(), duration=hh)
        free_sessions = json.load(open("free_sessions.json"))
        if row["timestamp"] not in free_sessions:
            print(row)
            db.insert_free_session(row)
            free_sessions.append(row["timestamp"])
            json.dump(free_sessions, open("free_sessions.json", "w"))

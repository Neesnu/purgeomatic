import os
import sys
import requests
from datetime import datetime
import config

c = config.Config()
if not c.check("tautulliAPIkey", "sonarrAPIkey"):
    print("ERROR: Required Tautulli/Sonarr API key not set. Cannot continue.")
    sys.exit(1)

c.apicheck(c.sonarrHost, c.sonarrAPIkey)

# Load protected series IDs from file
protected = []
if os.path.exists("./protected"):
    with open("./protected", "r") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line.isdigit():
                protected.append(int(line))

# Load protected tags
try:
    protected_tags = [int(i) for i in c.sonarrProtectedTags.split(",")]
except Exception:
    protected_tags = []

print("--------------------------------------")
print(datetime.now().isoformat())

def extract_guids(resp_json):
    guids = []
    # Tautulli V2 response
    if isinstance(resp_json, dict) and 'response' in resp_json:
        data_section = resp_json['response'].get('data', {})
        # metadata object case
        if isinstance(data_section, dict) and 'metadata' in data_section and 'guids' in data_section['metadata']:
            guids = data_section['metadata']['guids']
        else:
            # data list entries
            for entry in data_section.get('data', []):
                data_field = entry.get('data', {})
                if 'guids' in data_field:
                    guids.extend(data_field['guids'])
    # fallback list-of-entries format
    elif isinstance(resp_json, list):
        for entry in resp_json:
            data_field = entry.get('data', {})
            if 'guids' in data_field:
                guids.extend(data_field['guids'])
    return guids


def purge(series):
    deletesize = 0
    tvdbid = None

    # Fetch metadata from Tautulli
    resp = requests.get(
        f"{c.tautulliHost}/api/v2/?apikey={c.tautulliAPIkey}&cmd=get_metadata&rating_key={series['rating_key']}"
    )
    guids = extract_guids(resp.json())
    for guid in guids:
        if isinstance(guid, str) and guid.startswith("tvdb://"):
            tvdbid = guid.split("tvdb://", 1)[1]
            break

    # Fetch Sonarr series list
    s_resp = requests.get(f"{c.sonarrHost}/api/v3/series?apiKey={c.sonarrAPIkey}")
    s_list = s_resp.json()

    # Match by TVDB ID or by title
    if tvdbid:
        try:
            tid = int(tvdbid)
        except ValueError:
            tid = None
        series_info = next((s for s in s_list if s.get('tvdbId') == tid), None) if tid else None
    else:
        series_info = next((s for s in s_list if s.get('title') == series['title']), None)

    if not series_info:
        return deletesize

    # Skip protected
    if series_info.get('tvdbId') in protected or any(tag in protected_tags for tag in series_info.get('tags', [])):
        return deletesize

    # Delete from Sonarr
    if not c.dryrun:
        requests.delete(
            f"{c.sonarrHost}/api/v3/series/{series_info['id']}?apiKey={c.sonarrAPIkey}&deleteFiles=true"
        )

    # Delete from Overseerr if enabled
    if not c.dryrun and c.overseerrAPIkey:
        try:
            headers = {"X-Api-Key": c.overseerrAPIkey}
            o = requests.get(
                f"{c.overseerrHost}/api/v1/search/?query=tvdb%3A{series_info['tvdbId']}",
                headers=headers,
            )
            odata = o.json()
            overseerr_id = None
            for result in odata.get('results', []):
                mi = result.get('mediaInfo', {})
                if mi.get('tvdbId') == series_info['tvdbId']:
                    overseerr_id = mi.get('id')
                    break
            if overseerr_id:
                requests.delete(
                    f"{c.overseerrHost}/api/v1/media/{overseerr_id}",
                    headers=headers,
                )
        except Exception as e:
            print("ERROR: Overseerr API error. Error message: " + str(e))

    action = "DRY RUN" if c.dryrun else "DELETED"
    size_gb = series_info.get('statistics', {}).get('sizeOnDisk', 0) / 1073741824
    print(
        f"{action}: {series['title']} | {size_gb:.2f}GB | Sonarr ID: {series_info['id']} | TVDB ID: {series_info['tvdbId']}"
    )
    return size_gb

# Main loop

today = round(datetime.now().timestamp())
totalsize = 0
count = 0

r = requests.get(
    f"{c.tautulliHost}/api/v2/?apikey={c.tautulliAPIkey}" \
    f"&cmd=get_library_media_info&section_id={c.tautulliTvSectionID}" \
    f"&length={c.tautulliNumRows}&refresh=true"
)
shows = r.json()

try:
    for series in shows.get('response', {}).get('data', {}).get('data', []):
        last_played = series.get('last_played')
        if last_played:
            days = (today - int(last_played)) / 86400
            if days > c.daysSinceLastWatch:
                totalsize += purge(series)
                count += 1
        else:
            if c.daysWithoutWatch > 0 and series.get('added_at') and series.get('play_count') is None:
                days = (today - int(series['added_at'])) / 86400
                if days > c.daysWithoutWatch:
                    totalsize += purge(series)
                    count += 1
except Exception as e:
    print(
        "ERROR: There was a problem connecting to Tautulli/Sonarr/Overseerr."
        " Please double-check your settings and API keys.\n\nError message:\n" + str(e)
    )
    sys.exit(1)

print(f"Total space reclaimed: {totalsize:.2f}GB")
print(f"Total items deleted:   {count}")

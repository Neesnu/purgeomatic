import os
import sys
import json
import requests
from datetime import datetime
import config

c = config.Config()
if not c.check("tautulliAPIkey", "radarrAPIkey"):
    print("ERROR: Required Tautulli/Radarr API key not set. Cannot continue.")
    sys.exit(1)

c.apicheck(c.radarrHost, c.radarrAPIkey)

protected = []
if os.path.exists("./protected"):
    with open("./protected", "r") as file:
        for line in file:
            line = line.split("#", 1)[0].strip()
            if line.isdigit():
                protected.append(int(line))

try:
    protected_tags = [int(i) for i in c.radarrProtectedTags.split(",")]
except Exception:
    protected_tags = []

print("--------------------------------------")
print(datetime.now().isoformat())

def extract_guids(resp_json):
    guids = []
    if isinstance(resp_json, dict) and 'response' in resp_json:
        data_section = resp_json['response'].get('data', {})
        if isinstance(data_section, dict) and 'metadata' in data_section and 'guids' in data_section['metadata']:
            guids = data_section['metadata']['guids']
        else:
            for entry in data_section.get('data', []):
                data_field = entry.get('data', {})
                if 'guids' in data_field:
                    guids.extend(data_field['guids'])
    elif isinstance(resp_json, list):
        for entry in resp_json:
            data_field = entry.get('data', {})
            if 'guids' in data_field:
                guids.extend(data_field['guids'])
    return guids


def purge(movie):
    deletesize = 0
    tmdbid = None

    # get metadata
    r = requests.get(
        f"{c.tautulliHost}/api/v2/?apikey={c.tautulliAPIkey}&cmd=get_metadata&rating_key={movie['rating_key']}"
    )
    guids = extract_guids(r.json())
    for guid in guids:
        if isinstance(guid, str) and guid.startswith("tmdb://"):
            tmdbid = guid.split("tmdb://", 1)[1]
            break

    # find in Radarr
    radarr_resp = requests.get(f"{c.radarrHost}/api/v3/movie?apiKey={c.radarrAPIkey}")
    radarr_list = radarr_resp.json()
    if tmdbid:
        try:
            tid = int(tmdbid)
            radarr = next((m for m in radarr_list if m.get('tmdbId') == tid), None)
        except ValueError:
            radarr = None
    else:
        radarr = next((m for m in radarr_list if m.get('title') == movie['title']), None)

    if not radarr:
        return deletesize

    # skip protected
    if radarr.get('tmdbId') in protected or any(tag in protected_tags for tag in radarr.get('tags', [])):
        return deletesize

    # delete from Radarr
    if not c.dryrun:
        requests.delete(
            f"{c.radarrHost}/api/v3/movie/{radarr['id']}?apiKey={c.radarrAPIkey}&deleteFiles=true"
        )

    # delete from Overseerr
    if not c.dryrun and c.overseerrAPIkey:
        try:
            headers = {"X-Api-Key": c.overseerrAPIkey}
            o = requests.get(
                f"{c.overseerrHost}/api/v1/movie/{radarr['tmdbId']}", headers=headers
            )
            overseerr = o.json()
            requests.delete(
                f"{c.overseerrHost}/api/v1/media/{overseerr['mediaInfo']['id']}", headers=headers
            )
        except Exception:
            print("ERROR: Unable to connect to Overseerr.")

    action = "DRY RUN" if c.dryrun else "DELETED"
    deletesize = int(movie.get('file_size', 0)) / 1073741824
    print(f"{action}: {movie['title']} | {deletesize:.2f}GB | Radarr ID: {radarr['id']} | TMDB ID: {radarr['tmdbId']}")
    return deletesize

# main

today = round(datetime.now().timestamp())
totalsize = 0
count = 0

r = requests.get(
    f"{c.tautulliHost}/api/v2/?apikey={c.tautulliAPIkey}&cmd=get_library_media_info&section_id={c.tautulliMovieSectionID}&length={c.tautulliNumRows}&refresh=true"
)
response = r.json()
for movie in response.get('response', {}).get('data', {}).get('data', []):
    last_played = movie.get('last_played')
    if last_played:
        days = (today - int(last_played)) / 86400
        if days > c.daysSinceLastWatch:
            totalsize += purge(movie)
            count += 1
    else:
        added = movie.get('added_at')
        if c.daysWithoutWatch > 0 and added and movie.get('play_count') is None:
            days = (today - int(added)) / 86400
            if days > c.daysWithoutWatch:
                totalsize += purge(movie)
                count += 1

print(f"Total space reclaimed: {totalsize:.2f}GB")
print(f"Total items deleted:   {count}")

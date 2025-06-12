import os
import config
import sys
import json
import requests
import argparse
from argparse import RawTextHelpFormatter

c = config.Config()
if not c.check("tautulliAPIkey", "radarrAPIkey"):
    print("ERROR: Required Tautulli/Radarr API key not set. Cannot continue.")
    sys.exit(1)

c.apicheck(c.radarrHost, c.radarrAPIkey)

parser = argparse.ArgumentParser(
    description="Enter a movie title as an argument to delete a movie from overseerr, radarr, and from the disk.\nDon't worry! You'll be prompted before it does a delete.\nSo that it is properly read, pass your title as:\n\n  --title=\"Search Title\"\n",
    formatter_class=RawTextHelpFormatter,
)
parser.add_argument(
    "--title",
    metavar="search title",
    type=str,
    nargs="?",
    help="The title to search for deletion.",
    required=True,
)
args = parser.parse_args()
if not isinstance(args.title, str) or len(args.title) < 1:
    parser.print_help(sys.stderr)
    sys.exit(1)


def purge(movie):
    deletesize = 0
    tmdbid = None

    # Retrieve metadata from Tautulli
    resp = requests.get(
        f"{c.tautulliHost}/api/v2/?apikey={c.tautulliAPIkey}&cmd=get_metadata&rating_key={movie['rating_key']}"
    )
    json_data = resp.json()

    # Extract GUIDs without jq
    guids = []
    if isinstance(json_data, dict) and 'response' in json_data:
        data_section = json_data['response'].get('data', {})
        # Case: metadata object
        if isinstance(data_section, dict) and 'metadata' in data_section and 'guids' in data_section['metadata']:
            guids = data_section['metadata'].get('guids', [])
        else:
            # Case: data list entries
            entries = data_section.get('data', [])
            for entry in entries:
                data_field = entry.get('data', {})
                if 'guids' in data_field:
                    guids.extend(data_field['guids'])
    elif isinstance(json_data, list):
        for entry in json_data:
            data_field = entry.get('data', {})
            if 'guids' in data_field:
                guids.extend(data_field['guids'])

    try:
        for guid in guids:
            if isinstance(guid, str) and guid.startswith("tmdb://"):
                tmdbid = guid.split("tmdb://", 1)[1]
                break
    except Exception as e:
        print(
            f"WARNING: {movie['title']}: Unexpected GUID metadata from Tautulli. "
            f"Please refresh your library's metadata in Plex. Using less-accurate 'search mode'. Error: {e}"
        )
        guids = []

    # Query Radarr
    radarr_resp = requests.get(f"{c.radarrHost}/api/v3/movie?apiKey={c.radarrAPIkey}")
    radarr_movies = radarr_resp.json()
    # Match by TMDB ID or by title
    if tmdbid and guids:
        try:
            tmdb_int = int(tmdbid)
            radarr = next(m for m in radarr_movies if m.get('tmdbId') == tmdb_int)
        except (StopIteration, ValueError):
            radarr = None
    else:
        radarr = next((m for m in radarr_movies if m.get('title') == movie['title']), None)

    if not radarr:
        print(f"No matching Radarr movie found for '{movie['title']}'.")
        return deletesize

    # Delete from Radarr
    if not c.dryrun:
        requests.delete(
            f"{c.radarrHost}/api/v3/movie/{radarr['id']}?apiKey={c.radarrAPIkey}&deleteFiles=true"
        )

    # Optionally delete from Overseerr
    if not c.dryrun and c.overseerrAPIkey:
        try:
            headers = {"X-Api-Key": c.overseerrAPIkey}
            o_resp = requests.get(
                f"{c.overseerrHost}/api/v1/movie/{radarr['tmdbId']}",
                headers=headers,
            )
            overseerr = o_resp.json()
            requests.delete(
                f"{c.overseerrHost}/api/v1/media/{overseerr['mediaInfo']['id']}",
                headers=headers,
            )
        except Exception:
            print("ERROR: Unable to connect to Overseerr.")

    action = "DRY RUN" if c.dryrun else "DELETED"
    print(
        f"{action}: {movie['title']} | Radarr ID: {radarr['id']} | TMDB ID: {radarr['tmdbId']}"
    )
    deletesize = int(movie.get('file_size', 0)) / 1073741824

    return deletesize


totalsize = 0
# Search Tautulli for the movie
search_resp = requests.get(
    f"{c.tautulliHost}/api/v2/?apikey={c.tautulliAPIkey}" \
    f"&cmd=get_library_media_info&section_id={c.tautulliMovieSectionID}" \
    f"&search={args.title}&refresh=true"
)
movies = search_resp.json()

try:
    data_list = movies['response']['data']['data']
    count = len(data_list)
    if count == 1:
        movie = data_list[0]
        confirm = input(
            f"Movie found:\n{movie['title']} ({movie['year']})\nDelete it? [N]: "
        ).lower()
        confirmation = 1 if confirm == 'y' else 0
    elif count > 1:
        print("[0] Delete nothing")
        for idx, mv in enumerate(data_list, start=1):
            print(f"[{idx}] {mv['title']} ({mv['year']})")
        if c.dryrun:
            print("DRY RUN MODE - no selected movies will be deleted")
        else:
            print("*** The selected movie will be deleted ***")
        try:
            confirmation = int(input("Choose a movie to delete [0]: "))
        except ValueError:
            print("No action taken.")
            sys.exit(0)
    else:
        print("I couldn't find your movie. Try a different search term.")
        sys.exit(0)

    if confirmation > 0:
        movie = data_list[confirmation - 1]
        reclaimed = purge(movie)
        print(f"Total space reclaimed: {reclaimed:.2f} GB")
    else:
        print("No action taken.")
except Exception as e:
    print(
        "ERROR: There was a problem connecting to Tautulli/Radarr/Overseerr. "
        "Please double-check your settings and API keys.\n\nError message:\n" + str(e)
    )
    sys.exit(1)

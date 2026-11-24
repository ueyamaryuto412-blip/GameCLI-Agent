"""Public evidence adapters for episode research. Fetching evidence never starts a game."""
import html
import json
import re
import unicodedata
from urllib.parse import urlencode

import requests


def fetch(url, **params):
    response = requests.get(url, params=params, timeout=(5, 12))
    response.raise_for_status()
    return response


class SteamCandidates:
    def __init__(self, tags, per_tag=4):
        self.tags = tags
        self.per_tag = per_tag

    def collect(self, profiles, app_ids):
        notes = []
        origins = {}
        if app_ids:
            origins = {app: ["指定候補"] for app in app_ids}
        else:
            for app in profiles:
                origins[app] = ["収録プロフィール"]
            for tag in self.tags:
                params = dict(tags=tag, filter="topsellers", category1=998, category3=2,
                              os="win", infinite=1, start=0, count=self.per_tag, cc="jp", l="japanese")
                try:
                    response = fetch("https://store.steampowered.com/search/results/", **params)
                    markup = response.json()["results_html"]
                    found = re.findall(r'data-ds-itemkey="App_(\d+)"', markup)
                    if not found:
                        notes.append(f"Steamタグ {tag} の検索結果を取得できませんでした。")
                    for app in list(dict.fromkeys(found))[:self.per_tag]:
                        origins.setdefault(int(app), []).append(response.url)
                except (requests.RequestException, ValueError, KeyError) as exc:
                    notes.append(f"Steamタグ {tag} の検索を取得できませんでした（{type(exc).__name__}）。")
        games = []
        for app, discovery in origins.items():
            try:
                response = fetch("https://store.steampowered.com/api/appdetails", appids=app, cc="jp", l="japanese")
                data = response.json()[str(app)]["data"]
                if (data["type"] != "game" or not data["platforms"].get("windows")
                        or data["release_date"]["coming_soon"]
                        or not any(c["id"] == 2 for c in data.get("categories", []))):
                    notes.append(f"Steam {app} は発売済みWindowsソロゲームの対象外。")
                    continue
                games.append({"id": str(app), "steam_app_id": app, "title": data["name"], "discovery": discovery,
                              "store_url": f"https://store.steampowered.com/app/{app}/",
                              "store_data_url": response.url,
                              "description": html.unescape(re.sub("<[^>]+>", "", data["short_description"]))[:800],
                              "genres": [g["description"] for g in data.get("genres", [])],
                              "steam_recommendations": data.get("recommendations", {}).get("total"),
                              "profile": profiles.get(app)})
            except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
                notes.append(f"Steam {app} の作品情報を取得できませんでした（{type(exc).__name__}）。")
        return games, notes


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


class LocalCatalog:
    """Explicit editorial candidates for games distributed outside Steam."""
    def __init__(self, games):
        self.games = games

    def collect(self, profiles, app_ids):
        if app_ids:
            raise ValueError("--app-id applies to Steam discovery")
        return [dict(game, profile=None, genres=game.get("genres", [])) for game in self.games], []


def label(field):
    return field.get("simpleText") or "".join(part.get("text", "") for part in field.get("runs", []))


class YouTubeExamples:
    def __init__(self, limit=3):
        self.limit = limit

    def collect(self, game):
        params = {"search_query": game["title"] + " 実況", "hl": "ja", "gl": "JP"}
        url = "https://www.youtube.com/results"
        result = {"search_url": url + "?" + urlencode(params), "status": "unavailable", "videos": []}
        try:
            page = fetch(url, **params).text
            marker = re.search(r"var ytInitialData\s*=\s*", page)
            if not marker:
                return result
            data, _ = json.JSONDecoder().raw_decode(page, marker.end())
            sections = data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]
            seen = set()
            for section in sections["contents"]:
                for item in section.get("itemSectionRenderer", {}).get("contents", []):
                    video = item.get("videoRenderer", {})
                    key = video.get("videoId", "")
                    title = label(video.get("title", {}))
                    if (not re.fullmatch(r"[A-Za-z0-9_-]{11}", key) or key in seen
                            or normalized(game["title"]) not in normalized(title)):
                        continue
                    seen.add(key)
                    result["videos"].append({"title": title, "url": "https://www.youtube.com/watch?v=" + key,
                                             "views_display": label(video.get("viewCountText", {})),
                                             "published_display": label(video.get("publishedTimeText", {}))})
                    if len(seen) >= self.limit:
                        break
                if len(seen) >= self.limit:
                    break
            result["status"] = "sampled" if seen else "no_title_match"
        except (requests.RequestException, KeyError, ValueError, TypeError):
            pass
        return result

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import time


USER_NAME = os.environ.get("USER_NAME", "mertucan")
TOKEN = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
TOTAL_REPOSITORIES = int(os.environ.get("TOTAL_REPOSITORIES", "47"))
SVG_FILES = ("dark_mode.svg", "light_mode.svg")
API_ROOT = "https://api.github.com"
LOCAL_TZ = dt.timezone(dt.timedelta(hours=3))
RATE_LIMITED = False
DISPLAY_LOC_TOTALS = {
    "additions": 4_208_214,
    "deletions": 108_772,
    "total": 4_099_442,
}
LANGUAGE_LABELS = {
    "Jupyter Notebook": "Jupyter",
    "TypeScript": "TS",
    "JavaScript": "JS",
}


def api_get(path, query=None, accept="application/vnd.github+json", use_token=True):
    url = f"{API_ROOT}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{USER_NAME}-profile-readme",
    }
    if TOKEN and use_token:
        headers["Authorization"] = f"Bearer {TOKEN}"

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned {error.code} for {path}: {detail}") from error


def safe_api_get(path, query=None, default=None, accept="application/vnd.github+json", retry_public=False):
    global RATE_LIMITED
    try:
        return api_get(path, query=query, accept=accept)
    except Exception as exc:
        message = str(exc).lower()
        if "rate limit" in message or "abuse detection" in message:
            RATE_LIMITED = True
        if retry_public and TOKEN:
            try:
                return api_get(path, query=query, accept=accept, use_token=False)
            except Exception as retry_exc:
                retry_message = str(retry_exc).lower()
                if "rate limit" in retry_message or "abuse detection" in retry_message:
                    RATE_LIMITED = True
                print(f"warning: {retry_exc}", file=sys.stderr)
        print(f"warning: {exc}", file=sys.stderr)
        return default


def plural(value, unit):
    return f"{value} {unit}{'' if value == 1 else 's'}"


def account_age(created_at):
    created = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
    today = dt.datetime.now(dt.timezone.utc).date()
    years = today.year - created.year
    months = today.month - created.month
    days = today.day - created.day

    if days < 0:
        previous_month = today.replace(day=1) - dt.timedelta(days=1)
        days += previous_month.day
        months -= 1
    if months < 0:
        months += 12
        years -= 1

    return ", ".join([plural(years, "year"), plural(months, "month"), plural(days, "day")])


def format_number(value):
    return f"{int(value):,}"


COMPUTER_LANGUAGES = {
    "CSS",
    "Dockerfile",
    "HTML",
    "JSON",
    "Markdown",
    "MDX",
    "Shell",
    "YAML",
}


def grouped_languages(repositories):
    counts = {}
    for repo in repositories:
        language = repo.get("language")
        if language:
            counts[language] = counts.get(language, 0) + 1

    if not counts:
        return "Learning in public", "HTML, CSS, JSON, YAML"

    sorted_names = [name for name, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)]
    programming = [name for name in sorted_names if name not in COMPUTER_LANGUAGES]
    computer = [name for name in sorted_names if name in COMPUTER_LANGUAGES]

    if not programming:
        programming = sorted_names[:4]
    for preferred_language in ("HTML", "CSS", "JSON"):
        if preferred_language not in computer:
            computer.append(preferred_language)

    programming = [LANGUAGE_LABELS.get(name, name) for name in programming[:4]]
    computer = [LANGUAGE_LABELS.get(name, name) for name in computer[:5]]
    return ", ".join(programming), ", ".join(computer)


def public_repositories():
    repos = []
    page = 1
    while True:
        batch = api_get(
            f"/users/{USER_NAME}/repos",
            {
                "type": "owner",
                "sort": "updated",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        if not batch:
            return repos
        repos.extend(batch)
        if len(batch) < 100:
            return repos
        page += 1


def commit_count_this_year():
    now = dt.datetime.now(dt.timezone.utc)
    year_start = dt.datetime(now.year, 1, 1, tzinfo=dt.timezone.utc).date().isoformat()
    year_end = dt.datetime(now.year, 12, 31, tzinfo=dt.timezone.utc).date().isoformat()
    data = safe_api_get(
        "/search/commits",
        {
            "q": f"author:{USER_NAME} committer-date:{year_start}..{year_end}",
            "per_page": 1,
        },
        default={},
        accept="application/vnd.github+json",
    )
    return data.get("total_count", 0) if isinstance(data, dict) else 0


def repo_commits(owner, repo):
    commits = []
    page = 1
    while True:
        batch = safe_api_get(
            f"/repos/{owner}/{repo}/commits",
            {"author": USER_NAME, "per_page": 100, "page": page},
            default=[],
            retry_public=True,
        )
        if not batch:
            return commits
        commits.extend(batch)
        if len(batch) < 100:
            return commits
        page += 1


def loc_totals(repositories):
    additions = 0
    deletions = 0
    commit_total = 0

    for repo in repositories:
        if repo.get("fork"):
            continue
        full_name = repo.get("full_name", "")
        if "/" not in full_name:
            continue

        owner, repo_name = full_name.split("/", 1)
        for commit in repo_commits(owner, repo_name):
            sha = commit.get("sha")
            if not sha:
                continue
            detail = safe_api_get(
                f"/repos/{owner}/{repo_name}/commits/{sha}",
                default={},
                retry_public=True,
            )
            stats = detail.get("stats", {}) if isinstance(detail, dict) else {}
            additions += stats.get("additions", 0)
            deletions += stats.get("deletions", 0)
            commit_total += 1
            time.sleep(0.12)

    return {
        "additions": additions,
        "deletions": deletions,
        "total": additions - deletions,
        "commits": commit_total,
        "rate_limited": RATE_LIMITED,
    }


def preserve_zero_loc(root, values):
    preserve_lower = values.get("loc_rate_limited", False)
    if not preserve_lower and (values["loc_add"] or values["loc_del"] or values["loc_data"]):
        return values

    for key in ("loc_data", "loc_add", "loc_del"):
        element = root.find(f".//*[@id='{key}']")
        if element is not None:
            text = (element.text or "").replace(",", "").strip()
            if text.isdigit() and int(text) > 0:
                existing = int(text)
                if preserve_lower:
                    values[key] = max(existing, int(values.get(key, 0)))
                else:
                    values[key] = existing
    return values


def find_and_replace(root, element_id, new_text):
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = str(new_text)


def justify_format(root, element_id, new_text, length=0):
    if isinstance(new_text, int):
        new_text = format_number(new_text)
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)

    dot_count = max(0, length - len(new_text))
    if dot_count <= 2:
        dot_string = {0: "", 1: " ", 2: ". "}[dot_count]
    else:
        dot_string = " " + ("." * dot_count) + " "
    find_and_replace(root, f"{element_id}_dots", dot_string)


def align_format(root, element_id, new_text, prefix_length, target_column=59):
    justify_format(root, element_id, new_text, target_column - prefix_length - 2)


def update_svg(filename, values):
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    tree = ET.parse(filename)
    root = tree.getroot()
    values = preserve_zero_loc(root, values)

    for element_id, value in values.items():
        find_and_replace(root, element_id, value)

    align_format(root, "age_data", values["age_data"], 9)
    justify_format(root, "repo_data", values["repo_data"], 6)
    justify_format(root, "star_data", values["star_data"], 14)
    justify_format(root, "commit_data", values["commit_data"], 22)
    justify_format(root, "follower_data", values["follower_data"], 10)
    justify_format(root, "loc_data", values["loc_data"], 10)
    justify_format(root, "loc_add", values["loc_add"])
    justify_format(root, "loc_del", values["loc_del"])

    tree.write(filename, encoding="utf-8", xml_declaration=True)


def main():
    now = dt.datetime.now(LOCAL_TZ)
    user = api_get(f"/users/{USER_NAME}")
    repositories = public_repositories()
    programming_languages, computer_languages = grouped_languages(repositories)
    loc = loc_totals(repositories)
    commit_count = max(loc["commits"], commit_count_this_year())

    values = {
        "age_data": account_age(user["created_at"]),
        "repo_data": TOTAL_REPOSITORIES,
        "contrib_data": user.get("public_repos", len(repositories)),
        "commit_data": commit_count,
        "star_data": sum(repo.get("stargazers_count", 0) for repo in repositories),
        "follower_data": user.get("followers", 0),
        "loc_data": DISPLAY_LOC_TOTALS["total"],
        "loc_add": DISPLAY_LOC_TOTALS["additions"],
        "loc_del": DISPLAY_LOC_TOTALS["deletions"],
        "loc_rate_limited": loc["rate_limited"],
        "lang_programming_data": programming_languages,
        "lang_computer_data": computer_languages,
        "github_data": user["login"],
    }

    for filename in SVG_FILES:
        update_svg(filename, values)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"profile_stats.py failed: {exc}", file=sys.stderr)
        raise

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


def api_post(path, payload, accept="application/vnd.github+json"):
    url = f"{API_ROOT}{path}"
    headers = {
        "Accept": accept,
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{USER_NAME}-profile-readme",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, headers=headers, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned {error.code} for {path}: {detail}") from error

    if result.get("errors"):
        raise RuntimeError(f"GitHub GraphQL returned errors: {result['errors']}")
    return result.get("data", {})


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


def safe_api_post(path, payload, default=None, accept="application/vnd.github+json"):
    global RATE_LIMITED
    try:
        return api_post(path, payload, accept=accept)
    except Exception as exc:
        message = str(exc).lower()
        if "rate limit" in message or "abuse detection" in message:
            RATE_LIMITED = True
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


def normalize_repo(repo):
    owner = repo.get("owner")
    owner_login = owner.get("login") if isinstance(owner, dict) else None
    language = repo.get("language")
    if isinstance(repo.get("primaryLanguage"), dict):
        language = repo["primaryLanguage"].get("name")

    full_name = repo.get("full_name") or repo.get("nameWithOwner")
    if not full_name and owner_login and repo.get("name"):
        full_name = f"{owner_login}/{repo['name']}"

    return {
        "full_name": full_name,
        "fork": bool(repo.get("fork") or repo.get("isFork")),
        "language": language,
        "stargazers_count": repo.get("stargazers_count", repo.get("stargazerCount", 0)),
        "owner_login": owner_login or (full_name.split("/", 1)[0] if full_name and "/" in full_name else ""),
    }


def merge_repositories(*repo_groups):
    repositories = {}
    for repo_group in repo_groups:
        for repo in repo_group:
            normalized = normalize_repo(repo)
            full_name = normalized.get("full_name")
            if full_name:
                repositories[full_name] = normalized
    return list(repositories.values())


def accessible_repositories():
    if not TOKEN:
        return [normalize_repo(repo) for repo in public_repositories()]

    repos = []
    page = 1
    while True:
        batch = safe_api_get(
            "/user/repos",
            {
                "affiliation": "owner,collaborator,organization_member",
                "visibility": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
            default=[],
        )
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    return merge_repositories(repos) or [normalize_repo(repo) for repo in public_repositories()]


def repositories_contributed_to():
    if not TOKEN:
        return []

    query = """
    query($login: String!, $cursor: String) {
      user(login: $login) {
        repositoriesContributedTo(
          first: 100
          after: $cursor
          includeUserRepositories: false
          contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]
        ) {
          nodes {
            name
            nameWithOwner
            isFork
            stargazerCount
            primaryLanguage {
              name
            }
            owner {
              login
            }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }
    }
    """

    repos = []
    cursor = None
    while True:
        data = safe_api_post("/graphql", {"query": query, "variables": {"login": USER_NAME, "cursor": cursor}}, default={})
        connection = data.get("user", {}).get("repositoriesContributedTo", {}) if isinstance(data, dict) else {}
        nodes = connection.get("nodes") or []
        repos.extend(nodes)
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    return merge_repositories(repos)


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


def repo_commit_stats(owner, repo, author_id):
    additions = 0
    deletions = 0
    commit_total = 0
    cursor = None
    query = """
    query($owner: String!, $name: String!, $authorId: ID!, $cursor: String) {
      repository(owner: $owner, name: $name) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 100, after: $cursor, author: {id: $authorId}) {
                nodes {
                  additions
                  deletions
                }
                pageInfo {
                  hasNextPage
                  endCursor
                }
              }
            }
          }
        }
      }
    }
    """

    while True:
        data = safe_api_post(
            "/graphql",
            {"query": query, "variables": {"owner": owner, "name": repo, "authorId": author_id, "cursor": cursor}},
            default={},
        )
        repository = data.get("repository") if isinstance(data, dict) else None
        target = (((repository or {}).get("defaultBranchRef") or {}).get("target") or {})
        history = target.get("history") or {}
        nodes = history.get("nodes") or []
        for commit in nodes:
            additions += commit.get("additions", 0)
            deletions += commit.get("deletions", 0)
            commit_total += 1

        page_info = history.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        time.sleep(0.08)

    return additions, deletions, commit_total


def loc_totals(repositories, author_id):
    additions = 0
    deletions = 0
    commit_total = 0

    for repo in repositories:
        full_name = repo.get("full_name", "")
        if "/" not in full_name:
            continue

        owner, repo_name = full_name.split("/", 1)
        repo_additions, repo_deletions, repo_commits = repo_commit_stats(owner, repo_name, author_id)
        additions += repo_additions
        deletions += repo_deletions
        commit_total += repo_commits

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
    author_id = user.get("node_id")
    accessible = accessible_repositories()
    contributed = repositories_contributed_to()
    repositories = merge_repositories(accessible, contributed)
    owned_repositories = [repo for repo in repositories if repo.get("owner_login", "").lower() == USER_NAME.lower()]
    programming_languages, computer_languages = grouped_languages(repositories)
    loc = loc_totals(repositories, author_id)
    commit_count = max(loc["commits"], commit_count_this_year())

    values = {
        "age_data": account_age(user["created_at"]),
        "repo_data": max(TOTAL_REPOSITORIES, len(owned_repositories)),
        "contrib_data": len(repositories),
        "commit_data": commit_count,
        "star_data": sum(repo.get("stargazers_count", 0) for repo in owned_repositories),
        "follower_data": user.get("followers", 0),
        "loc_data": loc["total"],
        "loc_add": loc["additions"],
        "loc_del": loc["deletions"],
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

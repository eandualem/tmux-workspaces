"""Validate pull request routing; release authorization is a separate owner decision."""

import json
import os
from pathlib import Path


def valid_target(base: str, head: str, same_repository: bool) -> bool:
    return base == "develop" or (base == "main" and head == "develop" and same_repository)


def main() -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    pr = event["pull_request"]
    base, head = pr["base"], pr["head"]
    if not valid_target(
        base["ref"], head["ref"], head["repo"]["full_name"] == base["repo"]["full_name"]
    ):
        raise SystemExit(
            "Topic PRs must target develop. Only a promotion from this repository's "
            "develop branch may target main; explicit owner authorization is also required."
        )
    print("PR routing valid. A release promotion still requires explicit owner authorization.")


if __name__ == "__main__":
    main()

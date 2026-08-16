"""Publish the operator-facing agent to Vertex AI Agent Engine.

Run from the repository root:

    export GOOGLE_CLOUD_PROJECT=sdl-cinema-2026
    export GOOGLE_CLOUD_LOCATION=us-central1
    export SDL_STAGING_BUCKET=gs://sdl-cinema-2026-agent-staging
    export SDL_API_BASE_URL=https://<your-cloud-run-service>.run.app
    python deploy/agent_engine.py

Requires `gcloud auth application-default login` and the aiplatform,
cloudbuild, artifactregistry and storage APIs enabled on the project.

Deploying replaces nothing by default — `--update <resource-name>` redeploys
an existing engine in place, and `--delete <resource-name>` removes one. Agent
Engine bills for a deployed engine whether or not it serves traffic, so an
engine left running between demos is a standing cost, not a free idle.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "api"
sys.path.insert(0, str(API_DIR))

# The agent's own runtime dependencies. `sdl` itself ships as an extra package
# rather than a requirement because it is not published to an index.
REQUIREMENTS = [
    "google-adk>=2.7",
    "google-cloud-aiplatform[agent_engines]>=1.164",
]


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set — see the docstring at the top of this file.")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", metavar="RESOURCE_NAME", help="redeploy an existing engine")
    parser.add_argument("--delete", metavar="RESOURCE_NAME", help="delete an engine and exit")
    parser.add_argument("--list", action="store_true", help="list deployed engines and exit")
    args = parser.parse_args()

    import vertexai
    from vertexai import agent_engines

    project = _require("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    vertexai.init(
        project=project,
        location=location,
        staging_bucket=os.environ.get("SDL_STAGING_BUCKET"),
    )

    if args.list:
        for engine in agent_engines.list():
            print(engine.resource_name, "-", engine.display_name)
        return

    if args.delete:
        agent_engines.get(args.delete).delete(force=True)
        print("deleted", args.delete)
        return

    # Read before building the agent so a missing value fails immediately
    # rather than after a multi-minute upload.
    api_base_url = _require("SDL_API_BASE_URL")
    _require("SDL_STAGING_BUCKET")

    from sdl.agent import SDLApiClient, build_agent

    agent = build_agent(SDLApiClient(api_base_url))

    # `extra_packages` paths are preserved relative to the working directory and
    # unpacked at the root of the deployed app. An absolute path would nest the
    # package under its whole filesystem path, and the remote import of `sdl`
    # would fail at startup with "No module named 'sdl'".
    os.chdir(API_DIR)

    common = {
        "requirements": REQUIREMENTS,
        "extra_packages": ["sdl"],
        "env_vars": {"SDL_API_BASE_URL": api_base_url},
        "display_name": "SDL release agent",
        "description": (
            "Answers release-readiness questions from bound evidence and a "
            "deterministic policy evaluator. Read-only."
        ),
    }

    if args.update:
        engine = agent_engines.update(resource_name=args.update, agent_engine=agent, **common)
    else:
        engine = agent_engines.create(agent, **common)

    print("resource name:", engine.resource_name)


if __name__ == "__main__":
    main()

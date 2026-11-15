"""Conservative defaults shared by Vercel's API service and care daemon.

Database credentials and provider secrets still come from the host environment.
The existing Settings validators reject missing production configuration.
"""

import os

DEFAULTS = {
    "APP_ENV": "production",
    "BACKGROUND_TASKS_ENABLED": "false",
    "DEMO_MODE": "false",
    "SEED_DEMO_DATA": "false",
    "SEED_HEALTH_DATA": "false",
    "REGISTRATION_ENABLED": "false",
    "PROACTIVE_CHECKINS_ENABLED": "false",
    "MESSAGING_CHANNEL": "console",
    "PHOTON_PLATFORM": "sms",
    "PHOTON_DELIVERY": "off",
}


def configure(environ=None):
    env = os.environ if environ is None else environ
    for key, value in DEFAULTS.items():
        env.setdefault(key, value)
    if env.get("VERCEL_ENV") == "production" and env["APP_ENV"] != "production":
        raise ValueError("A Vercel production deployment requires APP_ENV=production")
    if "GIT_COMMIT" not in env and env.get("VERCEL_GIT_COMMIT_SHA"):
        env["GIT_COMMIT"] = env["VERCEL_GIT_COMMIT_SHA"]

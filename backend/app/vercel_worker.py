"""Run the existing durable worker as a Vercel daemon, never inside an API request."""

from app.vercel_runtime import configure


def main():
    configure()
    from app.worker import main as run_worker

    return run_worker()


if __name__ == "__main__":
    main()

import argparse

from verify_release import verify


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    args = parser.parse_args()
    return verify(args.base_url)


if __name__ == "__main__":
    raise SystemExit(main())

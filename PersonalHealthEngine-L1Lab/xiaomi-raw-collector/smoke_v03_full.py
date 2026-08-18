import asyncio
from collections import Counter

from auth import load_xiaomi_credentials
from xiaomi_client import XiaomiHealthClient


START_DATE = "2026-08-10"
END_DATE = "2026-08-12"

EXPECTED = {
    "steps": 117,
    "sleep": 4,
    "heart_rate": 595,
    "spo2": 63,
    "stress": 102,
}

EXTRA_KEYS = [
    "calories",
    "resting_heart_rate",
    "abnormal_heart_beat",
]


async def main():
    print("=" * 76)
    print("XIAOMI INDEPENDENT CLIENT v0.3-B FULL REGRESSION")
    print("=" * 76)

    credentials = load_xiaomi_credentials()

    print("CREDENTIALS: OS KEYRING OK")

    client = XiaomiHealthClient(
        user_id=credentials.user_id,
        pass_token=credentials.pass_token,
        region="cn",
    )

    failed = False

    try:
        await client.connect()
        print("CONNECT: PASS")

        print()
        print("BASELINE DATASETS")
        print("-" * 76)

        for key, expected_count in EXPECTED.items():
            records = await client.fetch_fitness_key(
                key,
                START_DATE,
                END_DATE,
            )

            sid_counts = Counter(
                str(item.get("sid"))
                for item in records
            )

            malformed = sum(
                1
                for item in records
                if not {
                    "sid",
                    "key",
                    "time",
                    "value",
                }.issubset(item.keys())
            )

            matched = (
                len(records) == expected_count
                and malformed == 0
            )

            if not matched:
                failed = True

            print()
            print(f"[{key}]")
            print("count     :", len(records))
            print("expected  :", expected_count)
            print("malformed :", malformed)
            print("sid count :", dict(sid_counts))
            print(
                "result     :",
                "PASS" if matched else "DIFFERENCE",
            )

        print()
        print("EXTENDED DATASETS")
        print("-" * 76)

        for key in EXTRA_KEYS:
            records = await client.fetch_fitness_key(
                key,
                START_DATE,
                END_DATE,
            )

            sid_counts = Counter(
                str(item.get("sid"))
                for item in records
            )

            malformed = sum(
                1
                for item in records
                if not {
                    "sid",
                    "key",
                    "time",
                    "value",
                }.issubset(item.keys())
            )

            if malformed:
                failed = True

            print()
            print(f"[{key}]")
            print("count     :", len(records))
            print("malformed :", malformed)
            print("sid count :", dict(sid_counts))

        print()
        print("SPORT ENDPOINT")
        print("-" * 76)

        sports = await client.fetch_sport_records(
            START_DATE,
            END_DATE,
        )

        print("count     :", len(sports))

        print()
        print("=" * 76)

        if failed:
            print("FULL REGRESSION: DIFFERENCES DETECTED")
        else:
            print("FULL REGRESSION: PASS")

        print("=" * 76)

        if failed:
            raise SystemExit(1)

    finally:
        await client.close()


asyncio.run(main())

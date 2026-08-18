import asyncio

from auth import load_xiaomi_credentials
from xiaomi_client import XiaomiHealthClient


START_DATE = "2026-08-10"
END_DATE = "2026-08-10"


async def main():

    print("=" * 72)
    print("XIAOMI INDEPENDENT CLIENT v0.3-A")
    print("=" * 72)

    credentials = (
        load_xiaomi_credentials()
    )

    print("CREDENTIALS: OS KEYRING OK")

    client = XiaomiHealthClient(
        user_id=credentials.user_id,
        pass_token=credentials.pass_token,
        region="cn",
    )

    try:
        await client.connect()

        print("CONNECT: PASS")

        steps = (
            await client.fetch_fitness_key(
                "steps",
                START_DATE,
                END_DATE,
            )
        )

        print()
        print("[steps]")
        print("range :", START_DATE, "->", END_DATE)
        print("count :", len(steps))

        required_fields = {
            "sid",
            "key",
            "time",
            "value",
        }

        malformed = 0

        for record in steps:
            if not required_fields.issubset(
                record.keys()
            ):
                malformed += 1

        print("malformed :", malformed)

        if len(steps) != 66:
            print()
            print(
                "NOTE: historical count differs "
                "from the previous capture."
            )
            print(
                "This is not automatically a "
                "client failure."
            )

        if not steps:
            raise RuntimeError(
                "No step records returned."
            )

        if malformed:
            raise RuntimeError(
                "Unexpected raw record structure."
            )

        print()
        print("=" * 72)
        print(
            "INDEPENDENT CLIENT SMOKE: PASS"
        )
        print("=" * 72)

    finally:
        await client.close()


asyncio.run(main())

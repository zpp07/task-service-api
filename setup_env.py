from pathlib import Path
from secrets import token_hex


if __name__ == "__main__":
    env_file = Path(__file__).with_name(".env")
    try:
        with env_file.open("x", encoding="ascii") as output:
            output.write(f"MYSQL_PASSWORD={token_hex(32)}\n")
            output.write(f"MYSQL_ROOT_PASSWORD={token_hex(32)}\n")
    except FileExistsError:
        print("Using existing .env")
    else:
        print("Created .env with local database passwords")

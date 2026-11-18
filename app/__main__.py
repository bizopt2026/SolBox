import uvicorn

from app.config import load_settings
from app.main import create_app


def main() -> None:
    s = load_settings()
    uvicorn.run(
        create_app(s),
        host=s.host,
        port=s.port,
        ssl_certfile=s.ssl_certfile,
        ssl_keyfile=s.ssl_keyfile,
    )


if __name__ == "__main__":
    main()

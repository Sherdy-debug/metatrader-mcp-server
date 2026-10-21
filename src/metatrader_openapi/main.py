# pylint: disable=import-error
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware
from .config import Settings
from .routers import router as api_router
import os
import argparse
import uvicorn
from dotenv import load_dotenv
from metatrader_mcp.utils import init
from metatrader_mcp.security import BearerTokenMiddleware, check_network_security, env_flag
from contextlib import asynccontextmanager

load_dotenv()

# Instantiate settings
settings = Settings()

# Define a lifespan handler for MT5 client lifecycle
@asynccontextmanager
async def lifespan(app):
    # Load environment and support uppercase or lowercase vars
    load_dotenv()
    login = os.getenv("LOGIN", os.getenv("login"))
    password = os.getenv("PASSWORD", os.getenv("password"))
    server = os.getenv("SERVER", os.getenv("server"))
    path = os.getenv("MT5_PATH", os.getenv("mt5_path"))
    client = init(login, password, server, path)
    app.state.client = client
    yield
    if client:
        client.disconnect()

# Initialize FastAPI app with OpenAPI metadata and lifespan
app = FastAPI(
    title=settings.title,
    version=settings.version,
    openapi_url=settings.openapi_url,
    docs_url=settings.docs_url,
    redoc_url=settings.redoc_url,
    lifespan=lifespan,
)

# Cross-origin access is disabled unless origins are listed in OPENAPI_CORS_ORIGINS
# (comma separated). A wildcard would let any web page open in a browser on this
# machine send orders to the local API.
cors_origins = [o.strip() for o in os.getenv("OPENAPI_CORS_ORIGINS", "").split(",") if o.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

# Bearer token authentication, required when listening on a non-loopback address.
auth_token = os.getenv("OPENAPI_AUTH_TOKEN") or None
if auth_token:
    app.add_middleware(BearerTokenMiddleware, token=auth_token)

# Include API routers
def strip_prefix(route: APIRoute) -> str:
    op_id = route.name
    prefix = "api_v1_"
    if op_id.startswith(prefix):
        op_id = op_id[len(prefix):]
    return op_id
app.include_router(api_router, prefix="/api/v1", generate_unique_id_function=strip_prefix)

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="MetaTrader OpenAPI server")
    parser.add_argument("--login", required=True, help="MT5 login")
    parser.add_argument("--password", required=True, help="MT5 password")
    parser.add_argument("--server", required=True, help="MT5 server address")
    parser.add_argument("--path", default=None, help="Path to MT5 terminal executable (optional, auto-detected if not provided)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--ssl-certfile", default=os.getenv("OPENAPI_SSL_CERTFILE"), help="TLS certificate file (env: OPENAPI_SSL_CERTFILE)")
    parser.add_argument("--ssl-keyfile", default=os.getenv("OPENAPI_SSL_KEYFILE"), help="TLS private key file (env: OPENAPI_SSL_KEYFILE)")
    args = parser.parse_args()

    try:
        check_network_security(
            args.host,
            token=auth_token,
            ssl_certfile=args.ssl_certfile,
            ssl_keyfile=args.ssl_keyfile,
            allow_insecure_remote=env_flag("OPENAPI_ALLOW_INSECURE_REMOTE"),
            token_env="OPENAPI_AUTH_TOKEN",
        )
    except ValueError as exc:
        parser.error(str(exc))

    # set both uppercase and lowercase env vars for CLI
    os.environ["LOGIN"] = args.login
    os.environ["PASSWORD"] = args.password
    os.environ["SERVER"] = args.server
    os.environ["login"] = args.login
    os.environ["password"] = args.password
    os.environ["server"] = args.server
    if args.path:
        os.environ["MT5_PATH"] = args.path

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
    )

if __name__ == "__main__":
    main()

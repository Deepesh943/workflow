import os
from dotenv import load_dotenv
load_dotenv()
# Fixed: env var name was "SECRET KEY" (space) — standard name uses underscore
secret_key = os.getenv("SECRET_KEY")
db_host = os.getenv("DB_HOST","localhost")
db_user = os.getenv("DB_USER","root")
db_password = os.getenv("DB_PASSWORD")
db_name = os.getenv("DB_NAME","smart_workflow")
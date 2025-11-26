import os

import ee
from dotenv import load_dotenv
from pytest import fixture

load_dotenv()


@fixture(scope="session")
def gcp_project_id():
    return os.environ["GCP_PROJECT_ID"]


@fixture(scope="session")
def hv_url():
    return os.environ.get("HV_URL", "https://earthengine-highvolume.googleapis.com")


@fixture(scope="session")
def service_account_email():
    return os.environ["SERVICE_ACCOUNT_EMAIL"]


@fixture(scope="session")
def service_account_credentials(service_account_email):
    credentials = ee.ServiceAccountCredentials(
        service_account_email, os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    )
    return credentials


@fixture(scope="session")
def ee_initialize(service_account_credentials, gcp_project_id, hv_url):
    ee.Initialize(credentials=service_account_credentials, project=gcp_project_id, url=hv_url)

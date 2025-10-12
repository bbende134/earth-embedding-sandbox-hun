import os

import ee
from dotenv import load_dotenv
from pytest import fixture

load_dotenv()


@fixture(scope="session")
def GCP_PROJECT_ID():
    return os.environ["GCP_PROJECT_ID"]


@fixture(scope="session")
def HV_URL():
    return os.environ.get("HV_URL", "https://earthengine-highvolume.googleapis.com")


@fixture(scope="session")
def SERVICE_ACCOUNT_EMAIL():
    return os.environ["service_account_email"]


@fixture(scope="session")
def SERVICE_ACCOUNT_CREDENTIALS(SERVICE_ACCOUNT_EMAIL):
    credentials = ee.ServiceAccountCredentials(
        SERVICE_ACCOUNT_EMAIL, os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    )
    return credentials


@fixture(scope="session")
def ee_initialize(SERVICE_ACCOUNT_CREDENTIALS, GCP_PROJECT_ID, HV_URL):
    ee.Initialize(credentials=SERVICE_ACCOUNT_CREDENTIALS, project=GCP_PROJECT_ID, url=HV_URL)
